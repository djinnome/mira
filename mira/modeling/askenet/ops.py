import copy
import sympy
from mira.metamodel import SympyExprStr
import mira.metamodel.ops as tmops
from mira.sources.askenet.petrinet import template_model_from_askenet_json
from .petrinet import template_model_to_petrinet_json
from mira.metamodel.io import mathml_to_expression
from mira.metamodel.template_model import Parameter, Distribution, Observable
from mira.metamodel.templates import Concept


def amr_to_mira(func):
    def wrapper(amr, *args, **kwargs):
        tm = template_model_from_askenet_json(amr)
        result = func(tm, *args, **kwargs)
        amr = template_model_to_petrinet_json(result)
        return amr

    return wrapper


# Edit ID / label / name of State, Transition, Observable, Parameter, Initial
@amr_to_mira
def replace_state_id(tm, old_id, new_id):
    """Replace the ID of a state."""
    concepts_name_map = tm.get_concepts_name_map()
    if old_id not in concepts_name_map:
        raise ValueError(f"State with ID {old_id} not found in model.")
    for template in tm.templates:
        for concept in template.get_concepts():
            if concept.name == old_id:
                concept.name = new_id
        template.rate_law = SympyExprStr(
            template.rate_law.args[0].subs(sympy.Symbol(old_id),
                                           sympy.Symbol(new_id)))
    for observable in tm.observables.values():
        observable.expression = SympyExprStr(
            observable.expression.args[0].subs(sympy.Symbol(old_id),
                                               sympy.Symbol(new_id)))
    for key, initial in copy.deepcopy(tm.initials).items():
        if initial.concept.name == old_id:
            tm.initials[key].concept.name = new_id
            # If the key is same as the old ID, we replace that too
            if key == old_id:
                tm.initials[new_id] = tm.initials.pop(old_id)
    return tm


@amr_to_mira
def replace_transition_id(tm, old_id, new_id):
    """Replace the ID of a transition."""
    for template in tm.templates:
        if template.name == old_id:
            template.name = new_id
    return tm


@amr_to_mira
def replace_observable_id(tm, old_id, new_id, display_name):
    """Replace the ID of an observable."""
    for obs, observable in copy.deepcopy(tm.observables).items():
        if obs == old_id:
            observable.name = new_id
            observable.display_name = display_name
            tm.observables[new_id] = observable
            tm.observables.pop(old_id)
    return tm


@amr_to_mira
def remove_observable_or_parameter(tm, replaced_id, replacement_value=None):
    if replacement_value:
        tm.substitute_parameter(replaced_id, replacement_value)
    else:
        for obs, observable in copy.deepcopy(tm.observables).items():
            if obs == replaced_id:
                tm.observables.pop(obs)
    return tm


@amr_to_mira
def add_observable(tm, new_id, new_display_name, new_rate_law):
    if new_id in tm.observables:
        print('This observable id is already present')
        return tm
    rate_law_sympy = mathml_to_expression(new_rate_law)
    new_observable = Observable(name=new_id, display_name=new_display_name, expression=rate_law_sympy)
    tm.observables[new_id] = new_observable
    return tm


@amr_to_mira
def replace_parameter_id(tm, old_id, new_id):
    """Replace the ID of a parameter."""
    for template in tm.templates:
        if template.rate_law:
            template.rate_law = SympyExprStr(
                template.rate_law.args[0].subs(sympy.Symbol(old_id),
                                               sympy.Symbol(new_id)))
    for observable in tm.observables.values():
        observable.expression = SympyExprStr(
            observable.expression.args[0].subs(sympy.Symbol(old_id),
                                               sympy.Symbol(new_id)))
    for key, param in copy.deepcopy(tm.parameters).items():
        if param.name == old_id:
            try:
                popped_param = tm.parameters.pop(param.name)
                popped_param.name = new_id
                tm.parameters[new_id] = popped_param
            except KeyError:
                print('Old id: {}, is not present in the parameter dictionary of the template model'.format(old_id))
    return tm


# Resolve issue where only parameters are added only when they are present in rate laws.
@amr_to_mira
def add_parameter(tm, parameter_id: str, expression_xml: str, value: float, distribution_type: str,
                  min_value: float, max_value: float):
    distribution = Distribution(type=distribution_type,
                                parameters={
                                    'maximum': max_value,
                                    'minimum': min_value
                                })
    sympy_expression = mathml_to_expression(expression_xml)
    data = {
        'name': parameter_id,
        'value': value,
        'distribution': distribution,
        'units': {'expression': sympy_expression,
                  'expression_mathml': expression_xml}
    }

    new_param = Parameter(**data)
    tm.parameters[parameter_id] = new_param

    return tm


@amr_to_mira
def replace_initial_id(tm, old_id, new_id):
    """Replace the ID of an initial."""
    tm.initials = {
        (new_id if k == old_id else k): v for k, v in tm.initials.items()
    }
    return tm


# Remove state
@amr_to_mira
def remove_state(tm, state_id):
    new_templates = []
    for template in tm.templates:
        to_remove = False
        for concept in template.get_concepts():
            if concept.name == state_id:
                to_remove = True
        if not to_remove:
            new_templates.append(template)
    tm.templates = new_templates

    for obs, observable in tm.observables.items():
        observable.expression = SympyExprStr(
            observable.expression.args[0].subs(sympy.Symbol(state_id), 0))
    return tm


# Remove transition
@amr_to_mira
def remove_transition(tm, transition_id):
    tm.templates = [t for t in tm.templates if t.name != transition_id]
    return tm


# @amr_to_mira
# def add_transition(tm, rate_law, src_id=None, tgt_id=None):
#     if not src_id and not tgt_id:
#         print("You must pass in at least one of source and target id")
#         return tm
#     sympy_expression = mathml_to_expression(rate_law)
#     if src_id is None and tgt_id is not None:
#         pass
#     if src_id is not None and tgt_id is None:
#         pass
#     else:
#         pass


@amr_to_mira
# rate law is of type Sympy Expression
def replace_rate_law_sympy(tm, transition_id, new_rate_law):
    for template in tm.templates:
        if template.name == transition_id:
            template.rate_law = SympyExprStr(new_rate_law)
    return tm


def replace_rate_law_mathml(tm, transition_id, new_rate_law):
    new_rate_law_sympy = mathml_to_expression(new_rate_law)
    return replace_rate_law_sympy(tm, transition_id, new_rate_law_sympy)


# currently initials don't support expressions so only implement the following 2 methods for observables
# if we are seeking to replace an expression in an initial, return current template model
@amr_to_mira
def replace_expression_sympy(tm, object_id, new_expression_sympy, initial_flag):
    if initial_flag:
        return tm
    else:
        for obs, observable in tm.observables.items():
            if obs == object_id:
                observable.expression = SympyExprStr(new_expression_sympy)
    return tm


def replace_expression_mathml(tm, object_id, new_expression_mathml, initial_flag):
    new_expression_sympy = mathml_to_expression(new_expression_mathml)
    return replace_expression_sympy(tm, object_id, new_expression_sympy, initial_flag)


@amr_to_mira
def stratify(*args, **kwargs):
    return tmops.stratify(*args, **kwargs)


@amr_to_mira
def simplify_rate_laws(*args, **kwargs):
    return tmops.simplify_rate_laws(*args, **kwargs)


@amr_to_mira
def aggregate_parameters(*args, **kwargs):
    return tmops.aggregate_parameters(*args, **kwargs)


@amr_to_mira
def counts_to_dimensionless(*args, **kwargs):
    return tmops.counts_to_dimensionless(*args, **kwargs)
