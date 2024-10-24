"""This module implements parsing Petri net models defined in
https://github.com/DARPA-ASKEM/Model-Representations/tree/main/petrinet.

MIRA TemplateModel representation limitations to keep in mind:
- Model version not supported
- Model schema not supported
- Initials only have a value, cannot be expressions so information on
  initial condition parameter relationship is lost
"""
__all__ = ["model_from_url", "model_from_json_file", "template_model_from_askenet_json"]

import json

import sympy
import requests

from mira.metamodel import *


def model_from_url(url: str) -> TemplateModel:
    """Return a model from a URL

    Parameters
    ----------
    url :
        The URL to the JSON file.

    Returns
    -------
    :
        A TemplateModel object.
    """
    res = requests.get(url)
    model_json = res.json()
    return template_model_from_askenet_json(model_json)


def model_from_json_file(fname: str) -> TemplateModel:
    """Return a model from a JSON file.

    Parameters
    ----------
    fname :
        The path to the JSON file.

    Returns
    -------
    :
        A TemplateModel object.
    """
    with open(fname) as f:
        model_json = json.load(f)
    return template_model_from_askenet_json(model_json)


def template_model_from_askenet_json(model_json) -> TemplateModel:
    """Return a model from a JSON object.

    Parameters
    ----------
    model_json :
        The JSON object.

    Returns
    -------
    :
        A TemplateModel object.
    """
    # First we build a lookup of states turned into Concepts and then use
    # these as arguments to Templates
    model = model_json['model']
    concepts = {}
    for state in model.get('states', []):
        concepts[state['id']] = state_to_concept(state)

    # Next, we capture all symbols in the model, including states and
    # parameters. We also extract parameters at this point.
    symbols = {state_id: sympy.Symbol(state_id) for state_id in concepts}
    mira_parameters = {}
    for parameter in model.get('parameters', []):
        mira_parameters[parameter['id']] = parameter_to_mira(parameter)
        symbols[parameter['id']] = sympy.Symbol(parameter['id'])

    param_values = {p['id']: p['value'] for p in model.get('parameters', [])}

    # Next we process initial conditions
    initials = {}
    for state in model.get('states', []):
        initial_expression = state.get('initial', {}).get('expression')
        if initial_expression:
            initial_sympy = sympy.parse_expr(initial_expression,
                                             local_dict=symbols)
            initial_sympy = initial_sympy.subs(param_values)
            try:
                initial_val = float(initial_sympy)
            except TypeError:
                continue

            initial = Initial(concept=concepts[state['id']],
                              value=initial_val)
            initials[initial.concept.name] = initial

    # Now we iterate over all the transitions and build templates
    templates = []
    for transition in model.get('transitions', []):
        inputs = transition.get('input', [])
        outputs = transition.get('output', [])
        # Since inputs and outputs can contain the same state multiple times
        # and in general we want to preserve the number of times a state
        # appears, we identify controllers one by one, and remove them
        # from the input/output lists
        controllers = []
        both = set(inputs) & set(outputs)
        while both:
            shared = next(iter(both))
            controllers.append(shared)
            inputs.remove(shared)
            outputs.remove(shared)
            both = set(inputs) & set(outputs)
        # We can now get the appropriate concepts for each group
        input_concepts = [concepts[i] for i in inputs]
        output_concepts = [concepts[i] for i in outputs]
        controller_concepts = [concepts[i] for i in controllers]

        templates.extend(transition_to_templates(transition,
                                                 input_concepts,
                                                 output_concepts,
                                                 controller_concepts,
                                                 symbols))
    # Finally, we gather some model-level annotations
    name = model_json.get('name')
    description = model_json.get('description')
    anns = Annotations(name=name, description=description)
    return TemplateModel(templates=templates,
                         parameters=mira_parameters,
                         initials=initials,
                         annotations=anns)


def state_to_concept(state):
    """Return a Concept from a state"""
    name = state['name'] if state.get('name') else state['id']
    grounding = state.get('grounding', {})
    identifiers = grounding.get('identifiers', {})
    context = grounding.get('context', {})
    return Concept(name=name,
                   identifiers=identifiers,
                   context=context)


def parameter_to_mira(parameter):
    """Return a MIRA parameter from a parameter"""
    distr = Distribution(**parameter['distribution']) \
        if parameter.get('distribution') else None
    return Parameter(name=parameter['id'],
                     value=parameter.get('value'),
                     distribution=distr)


def transition_to_templates(transition, input_concepts, output_concepts,
                            controller_concepts, symbols):
    """Return a list of templates from a transition"""
    rate_law_expression = transition.get('rate', {}).get('expression')
    rate_law = sympy.parse_expr(rate_law_expression, local_dict=symbols) \
        if rate_law_expression else None
    if not controller_concepts:
        if not input_concepts:
            for output_concept in output_concepts:
                yield NaturalProduction(outcome=output_concept,
                                        rate_law=rate_law)
        elif not output_concepts:
            for input_concept in input_concepts:
                yield NaturalDegradation(subject=input_concept,
                                         rate_law=rate_law)
        else:
            for input_concept in input_concepts:
                for output_concept in output_concepts:
                    yield NaturalConversion(subject=input_concept,
                                            outcome=output_concept,
                                            rate_law=rate_law)
    else:
        if not (len(input_concepts) == 1 and len(output_concepts) == 1):
            return []
        if len(controller_concepts) == 1:
            yield ControlledConversion(controller=controller_concepts[0],
                                       subject=input_concepts[0],
                                       outcome=output_concepts[0],
                                       rate_law=rate_law)
        else:
            yield GroupedControlledConversion(controllers=controller_concepts,
                                              subject=input_concepts[0],
                                              outcome=output_concepts[0],
                                              rate_law=rate_law)
