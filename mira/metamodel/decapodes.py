__all__ = ["Decapode", "Variable", "TangentVariable", "Summation", "Summand", "Op1", "Op2"]
import sympy


class Decapode:
    def __init__(self,
                 variables,
                 op1s,
                 op2s,
                 summations,
                 tangent_variables):

        self.variables = variables
        self.op1s = op1s
        self.op2s = op2s
        self.summations = summations
        self.tangent_variables = tangent_variables

        # These methods create a mapping between variable id to variable symbol/sympy.expr if they are never a tgt/res for
        # a unary or binary operation respectively. Variable with id 7 is not a result for a binary operation
        # but is the target of a unary operation.
        self.variable_expression_map_op1 = {input_var.variable_id: input_var.symbol for input_var in
                                            self.get_only_inputs_op1()}
        self.variable_expression_map_op2 = {input_var.variable_id: input_var.symbol for input_var in
                                            self.get_only_inputs_op2()}
        self.variable_expression_map_both = {input_var.variable_id: input_var.symbol for input_var in
                                             self.get_only_inputs_both()}

        self.set_base_symbols = {var_name for var_name in self.variable_expression_map_both.values()}

    def get_only_outputs_op1(self):
        inputs = set()
        for op1 in self.op1s.values():
            inputs.add(op1.src)
        return set(self.variables.values()) - inputs

    def get_only_outputs_op2(self):
        inputs = set()
        for op2 in self.op2s.values():
            inputs.add(op2.proj1)
            inputs.add(op2.proj2)
        return set(self.variables.values()) - inputs

    # Want to see if src for an operation1 is a res for operation2
    def get_only_inputs_op1(self):
        outputs = set()
        for op1 in self.op1s.values():
            outputs.add(op1.tgt)
        return set(self.variables.values()) - outputs

    def get_only_inputs_op2(self):
        outputs = set()
        for op2 in self.op2s.values():
            outputs.add(op2.res)
        return set(self.variables.values()) - outputs

    def get_only_inputs_both(self):
        outputs = set()
        for op1 in self.op1s.values():
            outputs.add(op1.tgt)
        for op2 in self.op2s.values():
            outputs.add(op2.res)
        return set(self.variables.values()) - outputs

    def get_op1_targets(self):
        return {op.tgt: op_id for op_id, op in self.op1s.items()}

    def get_op2_targets(self):
        return {op.res: op_id for op_id, op in self.op2s.items()}


class Variable:
    def __init__(self, variable_id, type, name, op1_list=None, op2_list=None):

        self.variable_id = variable_id
        self.type = type
        self.name = name
        self.symbol = sympy.Symbol(name)

        self.op1_list = op1_list
        self.op2_list = op2_list

        # find operations that have their result/target as the variable_id
        self.relevant_op_1 = [op1 for op1 in self.op1_list if op1['tgt'] == self.variable_id]
        self.relevant_op_2 = [op2 for op2 in self.op2_list if op2['res'] == self.variable_id]

        self.mapping_op1 = {}
        self.mapping_op2 = {}

        self.mapping_op1[self.variable_id] = []
        self.mapping_op2[self.variable_id] = []

        self.expression = None

        if not self.relevant_op_1 and not self.relevant_op_2:
            return

        # a variable id cannot be the result of multiple operations in op1 or op2 list
        # go through all the ops that have their target as self.variable_id
        for operation1 in self.relevant_op_1:
            # find all operations for unary operations where the src is a tgt
            self.find_srcs_for_op1(self.variable_id, operation1['src'], self.relevant_op_1)

        for operation2 in self.relevant_op_2:
            # find all operations for binary operations where proj1 is a res and where proj2 is a res
            self.find_srcs_for_op2(self.variable_id, operation2['proj1'], self.relevant_op_2)
            self.find_srcs_for_op2(self.variable_id, operation2['proj2'], self.relevant_op_2)

    def __repr__(self):
        return self.name

    def __str__(self):
        return self.__repr__()

    # recursive method for identifying unary operator sources
    def find_srcs_for_op1(self, parent_var, child_var, rel_op1_list):
        if parent_var not in self.mapping_op1:
            self.mapping_op1[parent_var] = []
        if child_var not in self.mapping_op1:
            self.mapping_op1[child_var] = []
        if not rel_op1_list:
            return
        for operator1 in rel_op1_list:
            if operator1 not in self.mapping_op1[parent_var]:
                self.mapping_op1[parent_var].append(operator1)
            unary_op_src = [op1 for op1 in self.op1_list if operator1['src'] == op1['tgt']]
            for unary_operator in unary_op_src:
                self.find_srcs_for_op1(unary_operator['tgt'], unary_operator['src'], unary_op_src)

    # recursion for finding binary operator sources
    def find_srcs_for_op2(self, parent_var, child_var, rel_op2_list):
        if parent_var not in self.mapping_op2:
            self.mapping_op2[parent_var] = []
        if child_var not in self.mapping_op2:
            self.mapping_op2[child_var] = []
        if not rel_op2_list:
            return

        # This list contains all operations where the parent_variable is the result of a binary operations
        for operator2 in rel_op2_list:
            if operator2 not in self.mapping_op2[parent_var]:
                self.mapping_op2[parent_var].append(operator2)

            # find all binary operations where proj1 and proj2 (children) are the result of binary operations
            src_proj1 = [op2 for op2 in self.op2_list if op2['res'] == operator2['proj1']]
            src_proj2 = [op2 for op2 in self.op2_list if op2['res'] == operator2['proj2']]
            for binary_operator_1 in src_proj1:
                self.find_srcs_for_op2(binary_operator_1['res'], binary_operator_1['proj1'], src_proj1)
                self.find_srcs_for_op2(binary_operator_1['res'], binary_operator_1['proj2'], src_proj1)
            for binary_operator_2 in src_proj2:
                self.find_srcs_for_op2(binary_operator_2['res'], binary_operator_2['proj1'], src_proj2)
                self.find_srcs_for_op2(binary_operator_2['res'], binary_operator_2['proj2'], src_proj2)

    def build_expression(self, decapode):
        # Variable built from unary operations
        if not self.mapping_op2[self.variable_id]:
            while self.variable_id not in decapode.variable_expression_map_op1:
                for mapping_var_id, operation in self.mapping_op1.items():
                    if mapping_var_id in decapode.variable_expression_map_op1:
                        continue
                    elif mapping_var_id not in decapode.variable_expression_map_op1:
                        if operation[0]['src'] in decapode.variable_expression_map_op1:
                            decapode.variable_expression_map_op1[mapping_var_id] = sympy.Function(operation[0]['op1'])(
                                decapode.variable_expression_map_op1[operation[0]['src']])
            self.expression = decapode.variable_expression_map_op1[self.variable_id]

        # Variable built from binary operations
        elif self.mapping_op2[self.variable_id]:
            while self.variable_id not in decapode.variable_expression_map_op2:
                for mapping_var_id, operation in self.mapping_op2.items():
                    if mapping_var_id in decapode.variable_expression_map_op2:
                        continue
                    elif mapping_var_id not in decapode.variable_expression_map_op2:
                        # if both proj 1 and proj 2 are in the variable expression map
                        if (operation[0]['proj1'] in decapode.variable_expression_map_op2 and operation[0]['proj2'] in
                            decapode.variable_expression_map_op2):
                            proj1_expression = decapode.variable_expression_map_op2[operation[0]['proj1']]
                            proj2_expression = decapode.variable_expression_map_op2[operation[0]['proj2']]
                            decapode.variable_expression_map_op2[mapping_var_id] = (
                                perform_binary_operation_sympy(operation[0]['op2'], proj1_expression, proj2_expression))
            self.expression = decapode.variable_expression_map_op2[self.variable_id]

        # It's a base-level varaible
        else:
            self.expression = decapode.variable_expression_map_both[self.variable_id]

    # Since this method relies on all variable expressions being accounted for the in the mappings, we have to run
    # build expression for each variable first before breaking down each non-base level variable in each variable's
    # expression
    def break_down_variables(self, decapode):
        var_set_symbols = {free_symbol for free_symbol in self.expression.free_symbols}
        while not var_set_symbols.issubset(decapode.set_base_symbols):
            # str_expression = str(self.expression)
            for free_symbol in var_set_symbols:
                # if the free symbol is not a base level variable and is an output of an operation
                if free_symbol not in decapode.set_base_symbols:
                    free_symbol_var_id = next(
                        var.variable_id for var in decapode.variables.values() if var.name == str(free_symbol))

                    # If the non-base level free symbol is the result of a binary operation
                    if decapode.variables[free_symbol_var_id].mapping_op2[free_symbol_var_id]:
                        self.expression = self.expression.subs(free_symbol,
                                                               decapode.variable_expression_map_op2[free_symbol_var_id])

                    # If the non-base level free symbol is the result of a unary operation
                    elif decapode.variables[free_symbol_var_id].mapping_op1[free_symbol_var_id]:
                        self.expression = self.expression.subs(free_symbol,
                                                               decapode.variable_expression_map_op1[free_symbol_var_id])
                    var_set_symbols = {free_symbol for free_symbol in self.expression.free_symbols}


def perform_binary_operation_sympy(operator, proj1, proj2):
    if operator == '/':
        return proj1 / proj2
    elif operator == '*':
        return proj1 * proj2
    elif operator == '+':
        return proj1 + proj2
    elif operator == '-':
        return proj1 - proj2
    elif operator == '^':
        return proj1 ** proj2


class TangentVariable:
    def __init__(self, tangent_id, tangent_var_id, variable):
        self.tangent_id = tangent_id
        self.tangent_var_id = tangent_var_id
        self.variable = variable


class Summation:
    def __init__(self, summation_id, summands, result_var_id):
        self.summation_id = summation_id
        self.summands = summands
        self.result_var_id = result_var_id
        self.sum = None

    # Can only run this after expressions have been built and variables have been broken down for each variable
    def add_variables(self):
        self.sum = self.summands[0].var.expression
        for summand in self.summands[1:]:
            self.sum = self.sum + summand.var.expression


class Summand:
    def __init__(self, summand_id, summand_var_id, summation_id, var):
        self.summand_id = summand_id
        self.summand_var_id = summand_var_id
        self.summation_id = summation_id
        self.var = var


class Op1:
    def __init__(self, src, tgt, op1):
        self.src = src
        self.tgt = tgt
        self.op1 = op1
        self.symbol = sympy.Function(op1)

    def __repr__(self):
        return f'Op1({self.src}, {self.tgt}, {self.op1})'

    def __str__(self):
        return self.__repr__()


class Op2:
    def __init__(self, proj1, proj2, res, op2):
        self.proj1 = proj1
        self.proj2 = proj2
        self.res = res
        self.op2 = op2
        self.symbol = sympy.Function(op2)

    def __repr__(self):
        return f'Op2({self.proj1}, {self.proj2}, {self.res}, {self.op2})'

    def __str__(self):
        return self.__repr__()
