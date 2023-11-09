from mira.metamodel.decapodes import *
from mira.sources.acsets.decapodes.util import PARTIAL_TIME_DERIVATIVE

__all__ = ["process_decaexpr"]


def get_variables_mapping_decaexpr(decaexpr_json):
    # First loop through the context to get the variables
    # then loop through the equations to get the remaining variables
    if "model" in decaexpr_json:
        decaexpr_json = decaexpr_json["model"]

    yielded_variable_names = set()
    var_dict = {
        ix: Variable(id=ix, type=_type, name=name)
        for ix, (name, _type) in enumerate(
            recursively_find_variables_decaexpr_json(
                decaexpr_json, yielded_variable_names
            )
        )
    }
    return var_dict


def recursively_find_variables_decaexpr_json(decaexpr_json, yielded_variables):
    """

    Parameters
    ----------
    decaexpr_json : dict | list
        The 'model' field of the decaexpr JSON
    yielded_variables : set
        The set of variables that have already been yielded

    Yields
    ------
    : tuple[str, str]
        A tuple of the variable type and name to be used to initialize the
        Variable class
    """
    assert isinstance(yielded_variables, set)

    # Yield variable type and name
    if isinstance(decaexpr_json, dict):
        if "_type" in decaexpr_json:
            # Under 'equation'
            if decaexpr_json["_type"] == "Var":
                name = decaexpr_json["name"]
                _type = "Form0"
                if name not in yielded_variables:
                    yield name, _type
                    yielded_variables.add(name)

            # Literal, under 'equation'
            elif decaexpr_json["_type"] == "Lit":
                name = decaexpr_json["name"]
                _type = "Literal"
                if name not in yielded_variables:
                    yield name, _type
                    yielded_variables.add(name)

            # Under 'context'
            elif decaexpr_json["_type"] == "Judgement":
                # type comes from the 'dim' field here
                name = decaexpr_json["var"]["name"]
                _type = decaexpr_json["dim"]
                if name not in yielded_variables:
                    yield name, _type
                    yielded_variables.add(name)

            # Top level
            elif decaexpr_json["_type"] == "DecaExpr":
                # Skip the header
                yield from recursively_find_variables_decaexpr_json(
                    decaexpr_json["context"], yielded_variables
                )
                yield from recursively_find_variables_decaexpr_json(
                    decaexpr_json["equations"], yielded_variables
                )

            # Equation object, under 'equations' yield from lhs and rhs
            elif decaexpr_json["_type"] == "Eq":
                yield from recursively_find_variables_decaexpr_json(
                    decaexpr_json["lhs"], yielded_variables
                )
                yield from recursively_find_variables_decaexpr_json(
                    decaexpr_json["rhs"], yielded_variables
                )

            # Derivative (tangent variable), under 'equations' -> 'lhs'/rhs'
            elif decaexpr_json["_type"] == "Tan":
                yield from recursively_find_variables_decaexpr_json(
                    decaexpr_json["var"], yielded_variables
                )

            # Multiplication, under 'equations' -> 'rhs'/lhs'
            elif decaexpr_json["_type"] == "Mult":
                for arg in decaexpr_json["args"]:
                    yield from recursively_find_variables_decaexpr_json(
                        arg, yielded_variables
                    )

            # Plus, under 'equations' -> 'rhs'/lhs'
            elif decaexpr_json["_type"] == "Plus":
                # A 'Plus' type means args is a list of terms to be summed over
                for term in decaexpr_json["args"]:
                    yield from recursively_find_variables_decaexpr_json(
                        term, yielded_variables
                    )

            # App1, under 'equations' -> 'rhs'/lhs' -> type='App1' -> 'arg'
            elif decaexpr_json["_type"] == "App1":
                # An 'App1' type means there is one argument, arg
                yield from recursively_find_variables_decaexpr_json(
                    decaexpr_json["arg"], yielded_variables
                )

            # App2, under 'equations' -> 'rhs'/lhs' -> type='Plus' -> 'args'
            elif decaexpr_json["_type"] == "App2":
                # An 'App2' type means there are two arguments, arg1 and arg2
                yield from recursively_find_variables_decaexpr_json(
                    decaexpr_json["arg1"], yielded_variables
                )
                yield from recursively_find_variables_decaexpr_json(
                    decaexpr_json["arg2"], yielded_variables
                )
            else:
                raise NotImplementedError(
                    f"Unhandled variable type: {decaexpr_json['_type']}"
                )
        else:
            for value in decaexpr_json.values():
                yield from recursively_find_variables_decaexpr_json(
                    value, yielded_variables
                )
    elif isinstance(decaexpr_json, list):
        for value in decaexpr_json:
            yield from recursively_find_variables_decaexpr_json(
                value, yielded_variables
            )
    else:
        raise NotImplementedError(
            f"Unhandled type: {type(decaexpr_json)}: {decaexpr_json}"
        )


def expand_equations(
    decaexpr_equations_json,
    variable_lookup,
    op2s_lookup,
    op1s_lookup,
    tangent_variables_lookup,
    summations_lookup,
    var_name_to_index,
) -> Variable:
    """Expand the equations in a decaexpr JSON to its components"""
    _type = decaexpr_equations_json["_type"]
    if _type in {"Var", "Lit"}:
        var_name = decaexpr_equations_json["name"]
        if var_name not in var_name_to_index:
            # Create new variable
            var_type = "Constant" if _type == "Lit" else "Form0"
            new_var_ix = len(variable_lookup)
            variable_lookup[new_var_ix] = Variable(
                id=new_var_ix, type=var_type, name=var_name
            )
            var_name_to_index[var_name] = new_var_ix
        return variable_lookup[var_name_to_index[var_name]]

    elif _type == "App2":
        # Binary operation
        arg1 = expand_equations(
            decaexpr_equations_json["arg1"],
            variable_lookup,
            op2s_lookup,
            op1s_lookup,
            tangent_variables_lookup,
            summations_lookup,
            var_name_to_index,
        )
        arg2 = expand_equations(
            decaexpr_equations_json["arg2"],
            variable_lookup,
            op2s_lookup,
            op1s_lookup,
            tangent_variables_lookup,
            summations_lookup,
            var_name_to_index,
        )
        op2 = decaexpr_equations_json["f"]
        # Create new variable that is the result of the binary operation
        var_type = "infer"
        if op2 == "*":
            name_prefix = "mult"
        elif op2 == "+":
            name_prefix = "add"
        elif op2 == "-":
            name_prefix = "sub"
        elif op2 == "/":
            name_prefix = "div"
        else:
            raise NotImplementedError(
                f"Unhandled binary operation: {op2}"
            )

        new_var_name_ix = len(
            [var.name for var in variable_lookup.values()
             if var.name.startswith(name_prefix)]
        ) + 1
        new_var_name = f"{name_prefix}_{new_var_name_ix}"

        new_var_ix = len(variable_lookup)
        variable_lookup[new_var_ix] = Variable(
            id=new_var_ix, type=var_type, name=new_var_name
        )

        # Add binary operation
        new_op2_ix = len(op2s_lookup)
        op2s_lookup[new_op2_ix] = Op2(
            id=new_op2_ix,
            proj1=variable_lookup[arg1.id],
            proj2=variable_lookup[arg2.id],
            res=variable_lookup[new_var_ix],
            op2=op2,
        )

        var_name_to_index[new_var_name] = new_var_ix

        return variable_lookup[new_var_ix]

    elif _type == "App1":
        # Unary operation; apply a function to an argument
        arg = expand_equations(
            decaexpr_equations_json["arg"],
            variable_lookup,
            op2s_lookup,
            op1s_lookup,
            tangent_variables_lookup,
            summations_lookup,
            var_name_to_index,
        )
        op1 = decaexpr_equations_json["f"]

        # Create new variable that is the result of the unary operation
        var_type = "infer"
        var_name = f"{op1}({arg.name})"

        new_var_ix = len(variable_lookup)
        variable_lookup[new_var_ix] = Variable(
            id=new_var_ix, type=var_type, name=var_name
        )

        var_name_to_index[var_name] = new_var_ix

        # Add unary operation
        new_op1_ix = len(op1s_lookup)
        op1s_lookup[new_op1_ix] = Op1(
            id=new_op1_ix,
            src=variable_lookup[arg.id],
            tgt=variable_lookup[new_var_ix],
            op1=op1,
        )

        return variable_lookup[new_var_ix]

    elif _type == "Tan":
        # Time derivative
        arg = expand_equations(
            decaexpr_equations_json["var"],
            variable_lookup,
            op2s_lookup,
            op1s_lookup,
            tangent_variables_lookup,
            summations_lookup,
            var_name_to_index,
        )

        # Create new variable that is the result of the unary operation
        var_type = "infer"
        var_name = f"{PARTIAL_TIME_DERIVATIVE}({arg.name})"

        new_var_ix = len(variable_lookup)
        variable_lookup[new_var_ix] = Variable(
            id=new_var_ix, type=var_type, name=var_name
        )

        var_name_to_index[var_name] = new_var_ix

        # Add unary operation
        new_op1_ix = len(op1s_lookup)
        op1s_lookup[new_op1_ix] = Op1(
            id=new_op1_ix,
            src=variable_lookup[arg.id],
            tgt=variable_lookup[new_var_ix],
            op1=PARTIAL_TIME_DERIVATIVE,
        )

        # Add tangent variable - the result of the derivative
        new_tangent_var_ix = len(tangent_variables_lookup)
        tangent_variables_lookup[new_tangent_var_ix] = TangentVariable(
            id=new_tangent_var_ix, incl_var=variable_lookup[new_var_ix]
        )

        return variable_lookup[new_var_ix]

    elif _type == "Mult":
        # Loop through the arguments and multiply them together to get the
        # result, start from the left
        new_mult_result = None
        new_var_ix = None
        for iter_ix in range(len(decaexpr_equations_json["args"]) - 1):
            if iter_ix == 0:
                # First iteration, create a new variable with the first two
                # arguments
                arg0 = expand_equations(
                    decaexpr_equations_json["args"][iter_ix],
                    variable_lookup,
                    op2s_lookup,
                    op1s_lookup,
                    tangent_variables_lookup,
                    summations_lookup,
                    var_name_to_index,
                )
                arg1 = expand_equations(
                    decaexpr_equations_json["args"][iter_ix + 1],
                    variable_lookup,
                    op2s_lookup,
                    op1s_lookup,
                    tangent_variables_lookup,
                    summations_lookup,
                    var_name_to_index,
                )
            else:
                # Subsequent iterations, use the result of the previous
                # iteration
                assert new_mult_result is not None, "Should not be None"
                arg0 = new_mult_result
                arg1 = expand_equations(
                    decaexpr_equations_json["args"][iter_ix + 1],
                    variable_lookup,
                    op2s_lookup,
                    op1s_lookup,
                    tangent_variables_lookup,
                    summations_lookup,
                    var_name_to_index,
                )

            # Create new variable that is the result of the multiplication
            var_type = "infer"
            new_mult_ix = len([var.name for var in variable_lookup.values() if
                               var.name.startswith("mult")]) + 1
            new_var_name = f"mult_{new_mult_ix}"

            new_var_ix = len(variable_lookup)
            variable_lookup[new_var_ix] = Variable(
                id=new_var_ix, type=var_type, name=new_var_name
            )

            var_name_to_index[new_var_name] = new_var_ix

            # Add binary operation
            new_op2_ix = len(op2s_lookup)
            op2s_lookup[new_op2_ix] = Op2(
                id=new_op2_ix,
                proj1=variable_lookup[arg0.id],
                proj2=variable_lookup[arg1.id],
                res=variable_lookup[new_var_ix],
                op2="*",
            )

            new_mult_result = variable_lookup[new_var_ix]

        assert new_var_ix is not None
        return variable_lookup[new_var_ix]

    elif _type == "Plus":
        # In decapode:
        #  - the Σ table specifies the result of the sums in the equation
        #  - the summand table specifies the terms in the sum(s), which sum
        #    they belong to is specified by the summation value which
        #    references one of the sums in the Σ table
        summand_list = []
        for summand_json in decaexpr_equations_json["args"]:
            summand_var = expand_equations(
                summand_json,
                variable_lookup,
                op2s_lookup,
                op1s_lookup,
                tangent_variables_lookup,
                summations_lookup,
                var_name_to_index,
            )
            summand_list.append(summand_var)

        # Create new variable that is the result of the addition
        var_type = "infer"
        new_add_ix = len([var.name for var in variable_lookup.values()
                          if var.name.startswith("add")]) + 1
        new_var_name = f"sum_{new_add_ix}"

        new_var_ix = len(variable_lookup)
        variable_lookup[new_var_ix] = Variable(
            id=new_var_ix, type=var_type, name=new_var_name
        )

        new_sum_ix = len(summations_lookup)
        summations_lookup[new_sum_ix] = Summation(
            id=new_sum_ix,
            summands=summand_list,
            sum=variable_lookup[new_var_ix],
        )

        var_name_to_index[new_var_name] = new_var_ix
        return variable_lookup[new_var_ix]

    else:
        raise NotImplementedError(f"Unhandled equation type: {_type}")


def process_decaexpr(decaexpr_json) -> Decapode:
    decaexpr_json_model = decaexpr_json["model"]
    variables = get_variables_mapping_decaexpr(decaexpr_json_model)
    name_to_variable_index = {v.name: k for k, v in variables.items()}

    op1s_lookup = {}
    op2_lookup = {}
    tangent_variables_lookup = {}
    summations_lookup = {}

    # Expand each side of the equation(s) into its components
    for equation_json in decaexpr_json_model["equations"]:
        lhs_result_var = expand_equations(
            equation_json["lhs"],
            variable_lookup=variables,
            op1s_lookup=op1s_lookup,
            op2s_lookup=op2_lookup,
            tangent_variables_lookup=tangent_variables_lookup,
            summations_lookup=summations_lookup,
            var_name_to_index=name_to_variable_index,
        )
        rhs_result_var = expand_equations(
            equation_json["rhs"],
            variable_lookup=variables,
            op1s_lookup=op1s_lookup,
            op2s_lookup=op2_lookup,
            tangent_variables_lookup=tangent_variables_lookup,
            summations_lookup=summations_lookup,
            var_name_to_index=name_to_variable_index,
        )

    return Decapode(
        variables=variables,
        op1s=op1s_lookup,
        op2s=op2_lookup,
        summations=summations_lookup,
        tangent_variables=tangent_variables_lookup,
    )
