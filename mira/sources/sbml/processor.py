"""
Alternate XPath queries for COPASI data:

1. ``copasi:COPASI/rdf:RDF/rdf:Description/bqbiol:hasProperty``
2. ``copasi:COPASI/rdf:RDF/rdf:Description/CopasiMT:is``
"""

import copy
import math
from typing import Dict, Iterable, List, Mapping, Tuple

from mira.sources.sbml.utils import *

logger = logging.getLogger(__name__)
logger.addHandler(TqdmLoggingHandler())


#: This XPath query gets annotations on species for their structured
#: identifiers, typically given as MIRIAM URIs or URNs
IDENTIFIERS_XPATH = f"rdf:RDF/rdf:Description/bqbiol:is/rdf:Bag/rdf:li"
COPASI_DESCR_XPATH = "/annotation/*[2]/rdf:RDF/rdf:Description"
COPASI_IS = "%s/CopasiMT:is" % COPASI_DESCR_XPATH
COPASI_IS_VERSION_OF = "%s/CopasiMT:isVersionOf" % COPASI_DESCR_XPATH
COPASI_HAS_PROPERTY = "%s/bqbiol:hasProperty" % COPASI_DESCR_XPATH
#: This is an alternative XPath for groundings that use the isVersionOf
#: relation and are thus less specific than the one above but can be used
#: as fallback
IDENTIFIERS_VERSION_XPATH = (
    f"rdf:RDF/rdf:Description/bqbiol:isVersionOf/rdf:Bag/rdf:li"
)
#: This XPath query gets annotations on species about their properties,
#: which typically help ad-hoc create subclasses that are more specific
PROPERTIES_XPATH = f"rdf:RDF/rdf:Description/bqbiol:hasProperty/rdf:Bag/rdf:li"
#: This query helps get annotations on reactions, like "this reaction is a
#: _protein-containing complex disassembly_ (GO:0043624)"
IS_VERSION_XPATH = f"rdf:RDF/rdf:Description/bqbiol:hasProperty/rdf:Bag/rdf:li"


class SbmlProcessor:
    """Traverse an SBML document generated by libSBML and extract a MIRA
    Template model."""

    def __init__(self, sbml_model, model_id=None, reporter_ids=None):
        self.sbml_model = sbml_model
        self.model_id = model_id
        self.reporter_ids = reporter_ids
        self.units = get_units(self.sbml_model.unit_definitions)

    def extract_model(self):
        if self.model_id is None:
            self.model_id = get_model_id(self.sbml_model, converter=converter)
        model_annots = get_model_annotations(
            self.sbml_model, converter=converter, logger=logger
        )
        reporter_ids = set(self.reporter_ids or [])
        concepts = self._extract_concepts()

        def _lookup_concepts_filtered(species_ids) -> List[Concept]:
            return [
                concepts[species_id]
                for species_id in species_ids
                if species_id not in reporter_ids
                and "cumulative" not in species_id
            ]

        # Iterate thorugh all reactions and piecewise convert to templates
        templates: List[Template] = []
        # see docs on reactions
        # https://sbml.org/software/libsbml/5.18.0/docs/formatted/python-api/
        # classlibsbml_1_1_reaction.html
        all_species = {species.id for species in self.sbml_model.species}

        all_parameters = {
            parameter.id: {
                "value": parameter.value,
                "description": parameter.name,
                "units": self.get_object_units(parameter),
            }
            for parameter in self.sbml_model.parameters
        }
        parameter_symbols = {
            parameter.id: sympy.Symbol(parameter.id)
            for parameter in self.sbml_model.parameters
        }
        compartment_symbols = {
            compartment.id: sympy.Symbol(compartment.id)
            for compartment in self.sbml_model.compartments
        }
        # Add compartment volumes as parameters
        for compartment in self.sbml_model.compartments:
            all_parameters[compartment.id] = {
                "value": compartment.volume,
                "description": compartment.name,
                "units": self.get_object_units(compartment),
            }

        # Handle custom function definitions in the model
        function_lambdas = {}
        for fun_def in self.sbml_model.function_definitions:
            args = [
                fun_def.getArgument(i).getName()
                for i in range(fun_def.getNumArguments())
            ]
            arg_symbols = {arg: sympy.Symbol(arg) for arg in args}

            signature = tuple(arg_symbols.values())
            formula_str = get_formula_str(fun_def.getBody())
            if isinstance(formula_str, float) and math.isnan(formula_str):
                continue
            formula = safe_parse_expr(formula_str, local_dict=arg_symbols)
            lmbd = sympy.Lambda(signature, formula)
            function_lambdas[fun_def.id] = lmbd

        # In formulas, the species ID appears instead of the species name
        # and so we have to map these to symbols corresponding to the species name
        species_id_map = {
            species.id: (
                sympy.Symbol(species.name)
                if (
                    species.name
                    and "(" not in species.name
                    and "-" not in species.name
                    and "+" not in species.name
                )
                else sympy.Symbol(species.id)
            )
            for species in self.sbml_model.species
        }

        all_locals = {
            k: v
            for k, v in (
                list(parameter_symbols.items())
                + list(compartment_symbols.items())
                + list(function_lambdas.items())
                + list(species_id_map.items())
            )
        }

        # Handle custom assignment rules in the model
        assignment_rules = {}
        for rule in self.sbml_model.rules:
            rule_expr = parse_assignment_rule(rule.formula, all_locals)
            if rule_expr:
                assignment_rules[rule.id] = rule_expr

        all_implicit_modifiers = set()
        implicit_modifiers = None
        for reaction in self.sbml_model.reactions:
            modifier_species = [
                species.species for species in reaction.modifiers
            ]
            reactant_species = [
                species.species for species in reaction.reactants
            ]
            product_species = [species.species for species in reaction.products]

            # In some models, modifiers are defined as both input and output
            # and can be reclassified as modifiers. Rather than using set logic
            # here we do this using lists to make sure we preserve stoichiometry
            while True:
                io_modifiers = set(reactant_species) & set(product_species)
                if not io_modifiers:
                    break
                io_mod = next(iter(io_modifiers))
                modifier_species.append(io_mod)
                reactant_species.remove(io_mod)
                product_species.remove(io_mod)

            rate_law = reaction.getKineticLaw()
            # Some rate laws define parameters locally and so we need to
            # extract them and add them to the global parameter list
            for parameter in rate_law.parameters:
                all_parameters[parameter.id] = {
                    "value": parameter.value,
                    "description": parameter.name if parameter.name else None,
                    "units": self.get_object_units(parameter),
                }
                parameter_symbols[parameter.id] = sympy.Symbol(parameter.id)

            rate_expr = safe_parse_expr(rate_law.formula, local_dict=all_locals)
            # At this point we need to make sure we substitute the assignments
            rate_expr = rate_expr.subs(assignment_rules)

            for comp, comp_symbol in compartment_symbols.items():
                # We want to handle the special case where the compartment is
                # just a constant 1.0 and so we can just remove it from the
                # rate expression but that requires some special handling otherwise
                # and explicit 1.0 will be carried around in the rate expression
                comp_one = False
                if comp_symbol in rate_expr.free_symbols:
                    if (
                        rate_expr
                        not in rate_expr.diff(comp_symbol).free_symbols
                    ):
                        if all_parameters[comp]["value"] == 1.0:
                            comp_one = True
                            rate_expr /= comp_symbol
                if not comp_one:
                    rate_expr = rate_expr.subs(
                        comp_symbol, all_parameters[comp]["value"]
                    )

            rate_law_variables = variables_from_sympy_expr(rate_expr)

            # Implicit modifiers appear in the rate law but are not reactants and
            # aren't listed explicitly as modifiers. They have to be proper species
            # though (since the rate law also contains parameters).
            implicit_modifiers = (set(rate_law_variables) & all_species) - (
                set(reactant_species) | set(modifier_species)
            )
            # If reversible, we remove any product terms from implicit
            # modifiers
            reversible = reaction.getReversible()
            if reversible:
                implicit_modifiers -= set(product_species)
            # We extend modifiers with implicit ones
            modifier_species += sorted(implicit_modifiers)
            all_implicit_modifiers |= implicit_modifiers

            modifiers = _lookup_concepts_filtered(modifier_species)
            reactants = _lookup_concepts_filtered(reactant_species)
            products = _lookup_concepts_filtered(product_species)

            # check if reaction is reversible (i.e., reversible=False in the attributes),
            # then add a backwards conversion.
            if len(reactants) == 1 and len(products) == 1:
                if reactants[0].name and reactants[0] == products[0]:
                    logger.debug(f"[{self.model_id} reaction:{reaction.id}]")
                    logger.debug(f"Same reactant and product: {reactants[0]}")
                    logger.debug(f"Modifiers: {modifiers}")
                    continue
                if len(modifiers) == 0:
                    if reversible:
                        templates.append(
                            ReversibleFlux(
                                left=[reactants],
                                right=[products],
                                rate_law=rate_expr,
                            )
                        )
                    else:
                        templates.append(
                            NaturalConversion(
                                subject=reactants[0],
                                outcome=products[0],
                                rate_law=rate_expr,
                            )
                        )
                elif len(modifiers) == 1:
                    templates.append(
                        ControlledConversion(
                            subject=reactants[0],
                            outcome=products[0],
                            controller=modifiers[0],
                            rate_law=rate_expr,
                        )
                    )
                else:
                    """
                    could be the case that there's a linear combination of things that are independent
                    - this could mean you could create multiple conversions

                    but, they can be dependent too, then harder to break up
                    """
                    templates.append(
                        GroupedControlledConversion(
                            subject=reactants[0],
                            outcome=products[0],
                            controllers=modifiers,
                            rate_law=rate_expr,
                        )
                    )
            elif len(reactants) >= 1 and len(products) >= 1 and not modifiers:
                if reversible:
                    template = ReversibleFlux(
                        left=reactants,
                        right=products,
                        rate_law=rate_expr,
                    )
                else:
                    template = MultiConversion(
                        subjects=reactants,
                        outcomes=products,
                        rate_law=rate_expr,
                    )
                templates.append(template)

            elif not reactants and not products:
                logger.debug(
                    f"[{self.model_id} reaction:{reaction.id}] missing reactants and products"
                )
                continue
            # We either have a production or a degradation
            elif bool(products) != bool(reactants):
                kwargs = {"rate_law": rate_expr}
                if not modifiers:
                    contr = {}
                elif len(modifiers) == 1:
                    contr = {"controller": modifiers[0]}
                else:
                    contr = {"controllers": modifiers}
                kwargs.update(contr)

                if products:
                    cls = (
                        NaturalProduction
                        if not modifiers
                        else (
                            ControlledProduction
                            if len(modifiers) == 1
                            else GroupedControlledProduction
                        )
                    )
                    kwargs.update({"outcome": products[0]})
                else:
                    cls = (
                        NaturalDegradation
                        if not modifiers
                        else (
                            ControlledDegradation
                            if len(modifiers) == 1
                            else GroupedControlledDegradation
                        )
                    )
                    kwargs.update({"subject": reactants[0]})
                templates.append(cls(**kwargs))
            else:
                logger.debug(
                    f"[{self.model_id} reaction:{reaction.id}] skipping reaction with multiple inputs/outputs"
                )
                for i, inp in enumerate(reactants):
                    logger.debug(f"reactant {i}: {inp!r}")
                for i, inp in enumerate(products):
                    logger.debug(f"products {i}: {inp!r}")
                logger.debug("")
                continue

        # Gather species-level initial conditions
        initials = {}
        for species in self.sbml_model.species:
            # Handle the case when there is no name
            key = species.getName() or species.getId()
            # In some models, initial_amount is used, and somewhat confusingly
            # initial_concentration is still a float value (even if not
            # defined in the XML) with value 0.0. So we have to do a more complex
            # check here.
            init_amount_falsy = not species.initial_amount
            init_conc_falsy = not species.initial_concentration
            if init_conc_falsy and not init_amount_falsy:
                init_value = species.initial_amount
            elif not init_conc_falsy:
                init_value = species.initial_concentration
            else:
                init_value = 0.0
            initials[key] = Initial(
                concept=concepts[key],
                expression=SympyExprStr(sympy.Float(init_value)),
            )

        param_objs = {
            k: Parameter(
                name=k,
                value=v["value"],
                description=v["description"],
                units=v["units"],
            )
            for k, v in all_parameters.items()
        }

        template_model = TemplateModel(
            templates=templates,
            parameters=param_objs,
            initials=initials,
            annotations=model_annots,
        )
        # Replace constant concepts by their initial value
        template_model = replace_constant_concepts(
            template_model, implicit_modifiers
        )
        return template_model

    def _extract_concepts(self) -> Mapping[str, Concept]:
        """Extract concepts from an SBML model."""
        concepts = {}
        # see https://sbml.org/software/libsbml/5.18.0/docs/formatted/python-api/classlibsbml_1_1_species.html
        for species in self.sbml_model.getListOfSpecies():
            # Extract the units for the species
            units = self.get_object_units(species)
            concept = _extract_concept(
                species, model_id=self.model_id, units=units
            )
            concepts[species.getId()] = concept

        return concepts

    def get_object_units(self, object):
        if object.units:
            if object.units == "dimensionless":
                return Unit(expression=sympy.Integer(1))
            else:
                return Unit(expression=self.units[object.units])
        else:
            return None


def find_constant_concepts(template_model: TemplateModel) -> Iterable[str]:
    # Find concepts that are unchanged, just appear as controllers or not at all
    all_concepts = set()
    changing_concepts = set()
    for template in template_model.templates:
        concepts_by_role = template.get_concepts_by_role()
        for role, concepts in concepts_by_role.items():
            names = (
                {c.name for c in concepts}
                if isinstance(concepts, list)
                else {concepts.name}
            )
            if role in {"subject", "outcome"}:
                changing_concepts |= names
            all_concepts |= names
    non_changing_concepts = all_concepts - changing_concepts
    return non_changing_concepts


def replace_constant_concepts(template_model: TemplateModel, candidates=None):
    """Replace concepts that are constant by parameters."""
    constant_concepts = find_constant_concepts(template_model)
    # If we have explicit candidates to consider, we just constrain
    # to those
    if candidates is not None:
        constant_concepts &= candidates
    for constant_concept in constant_concepts:
        initial = template_model.initials.get(constant_concept)
        if initial is not None:
            initial_expression = initial.expression
        else:
            initial_expression = SympyExprStr(sympy.Float(1.0))
        # Fixme, do we need more grounding (identifiers, concept)
        # for the concept here?
        # Get the units of the concept here
        template_model.parameters[constant_concept] = Parameter(
            name=constant_concept, value=float(initial_expression.args[0])
        )
        new_templates = []
        for template in template_model.templates:
            new_template = replace_controller_by_constant(
                template, constant_concept, initial_expression.args[0]
            )
            if new_template:
                new_templates.append(new_template)
            else:
                new_templates.append(template)
        template_model.templates = new_templates
    return template_model


def replace_controller_by_constant(template, controller_name, value):
    if isinstance(template, ControlledConversion):
        if template.controller.name == controller_name:
            new_template = NaturalConversion(
                subject=template.subject,
                outcome=template.outcome,
            )
            new_template.rate_law = template.rate_law.subs(
                controller_name, value
            )
            return new_template
    elif isinstance(template, GroupedControlledConversion):
        if len(template.controllers) > 2:
            new_template = GroupedControlledConversion(
                subject=template.subject,
                outcome=template.outcome,
                controllers=[
                    c for c in template.controllers if c.name != controller_name
                ],
            )
            new_template.rate_law = template.rate_law.subs(
                controller_name, value
            )
            return new_template
        else:
            # If there are only two controllers, we can replace the
            # GroupedControlledConversion with a ControlledConversion
            new_template = ControlledConversion(
                subject=template.subject,
                outcome=template.outcome,
                controller=template.controllers[0],
            )
            new_template.rate_law = template.rate_law.subs(
                controller_name, value
            )
            return new_template
    # TODO: potentially handle other template types
    return


def parse_assignment_rule(rule, locals):
    try:
        expr = safe_parse_expr(rule, local_dict=locals)
        return expr
    except SyntaxError:
        return None


def get_formula_str(ast_node):
    name = ast_node.getName()
    if not name:
        op = ast_node.getOperatorName()
        if op:
            if op == "times":
                op_str = "*"
            elif op == "plus":
                op_str = "+"
            elif op == "divide":
                op_str = "/"
            elif op == "minus":
                op_str = "-"
            else:
                print("Unknown op: %s" % op)
                assert False
            # Special case where we have a unary minus
            if op == "minus" and ast_node.isUMinus():
                return "-%s" % get_formula_str(ast_node.getChild(0))
            # More general binary case
            return "(%s %s %s)" % (
                get_formula_str(ast_node.getChild(0)),
                op_str,
                get_formula_str(ast_node.getChild(1)),
            )
        val = ast_node.getValue()
        if val is not None:
            return val
    # Exponential doesn't show up as an operator but rather a name
    elif name in {"exp"}:
        return "%s(%s)" % (name, get_formula_str(ast_node.getChild(0)))
    else:
        return name


def variables_from_sympy_expr(expr):
    """Recursively find variables appearing in a sympy expression."""
    variables = set()
    if isinstance(expr, sympy.Symbol):
        variables.add(expr.name)
    else:
        assert isinstance(expr, sympy.Expr)
        for arg in expr.args:
            variables |= variables_from_sympy_expr(arg)
    return variables


def variables_from_ast(ast_node):
    """Recursively find variables appearing in a libSbml math formula.

    Note: currently unused but not removed for now since it may become
    necessary again.
    """
    variables_in_ast = set()
    # We check for any children first
    for child_id in range(ast_node.getNumChildren()):
        child = ast_node.getChild(child_id)
        # If the child has further children, we recursively add its variables
        if child.getNumChildren():
            variables_in_ast |= variables_from_ast(child)
        # Otherwise we just add the "leaf" child variable
        else:
            variables_in_ast.add(child.getName())
    # Now we add the node itself. Note that sometimes names are None which
    # we can ignore.
    name = ast_node.getName()
    if name:
        variables_in_ast.add(name)
    return variables_in_ast


def _extract_concept(species, units=None, model_id=None):
    # Generally, species have an ID and a name
    species_id = species.getId()
    # If the name is missing, we revert to the ID as the name
    species_name = species.getName() or species_id
    if "(" in species_name or not species_name:
        species_name = species_id

    # If we have curated a grounding for this species we return the concept
    # directly.based on the mapping
    if (model_id, species_name) in grounding_map:
        mapped_ids, mapped_context = grounding_map[(model_id, species_name)]
        concept = Concept(
            name=species_name,
            display_name=species_name,
            identifiers=copy.deepcopy(mapped_ids),
            context=copy.deepcopy(mapped_context),
            units=units,
        )
        return concept
    else:
        logger.debug(
            f"[{model_id} species:{species_id}] not found in grounding map"
        )

    # Otherwise we try to create a Concept with all its groundings and apply
    # various normalizations and clean up.

    # The following traverses the annotations tag, which allows for
    # embedding arbitrary XML content. Typically, this is RDF.
    annotation_string = species.getAnnotationString()
    if not annotation_string:
        logger.debug(f"[{model_id} species:{species_id}] had no annotations")
        concept = Concept(
            name=species_name,
            display_name=species_name,
            identifiers={},
            context={},
            units=units,
        )
        return concept

    annotation_tree = etree.fromstring(annotation_string)

    rdf_properties = [
        converter.parse_uri(desc.attrib[RESOURCE_KEY])
        for desc in annotation_tree.findall(
            PROPERTIES_XPATH, namespaces=PREFIX_MAP
        )
    ]

    # First we check identifiers with a specific relation representing
    # equivalence
    identifiers_list = []
    for element in annotation_tree.findall(
        IDENTIFIERS_XPATH, namespaces=PREFIX_MAP
    ):
        curie = converter.parse_uri(element.attrib[RESOURCE_KEY])
        identifiers_list.append(curie)

    context = {}
    if ("ido", "C101887") in identifiers_list:
        identifiers_list.remove(("ido", "C101887"))
        identifiers_list.append(("ncit", "C101887"))
    if ("ncit", "C171133") in identifiers_list:
        identifiers_list.remove(("ncit", "C171133"))
    # Reclassify asymptomatic as a disease status
    if ("ido", "0000569") in identifiers_list and (
        "ido",
        "0000511",
    ) in identifiers_list:
        identifiers_list.remove(("ido", "0000569"))
        context["disease_status"] = "ncit:C3833"
    # Exposed shouldn't be susceptible
    if ("ido", "0000514") in identifiers_list and (
        "ido",
        "0000597",
    ) in identifiers_list:
        identifiers_list.remove(("ido", "0000514"))
    # Break apoart hospitalized and ICU
    if ("ncit", "C25179") in identifiers_list and (
        "ncit",
        "C53511",
    ) in identifiers_list:
        identifiers_list.remove(("ncit", "C53511"))
        context["disease_status"] = "ncit:C53511"
    # Remove redundant term for deceased due to disease progression
    if ("ncit", "C28554") in identifiers_list and (
        "ncit",
        "C168970",
    ) in identifiers_list:
        identifiers_list.remove(("ncit", "C168970"))

    identifiers = dict(identifiers_list)
    if len(identifiers) != len(identifiers_list):
        assert False, identifiers_list

    # We capture context here as a set of generic properties
    for idx, rdf_property in enumerate(sorted(rdf_properties)):
        if rdf_property[0] == "ncit" and rdf_property[1].startswith("000"):
            prop = ("ido", rdf_property[1])
        elif rdf_property[0] == "ido" and rdf_property[1].startswith("C"):
            prop = ("ncit", rdf_property[1])
        else:
            prop = rdf_property
        context[f'property{"" if idx == 0 else idx}'] = ":".join(prop)
    # As a fallback, we also check if identifiers are available with
    # a less specific relation
    if not identifiers:
        elements = sorted(
            [
                converter.parse_uri(element.attrib[RESOURCE_KEY])
                for element in annotation_tree.findall(
                    IDENTIFIERS_VERSION_XPATH, namespaces=PREFIX_MAP
                )
            ],
            reverse=True,
        )
        # This is generic COVID-19 infection, generally not needed
        if ("ncit", "C171133") in elements:
            elements.remove(("ncit", "C171133"))
        # Remap inconsistent groundings
        if ("ido", "0000569") in elements:
            elements.remove(("ido", "0000569"))
            elements.append(("ido", "0000511"))
            context["disease_status"] = "ncit:C3833"
        elif ("ido", "0000573") in elements:
            elements.remove(("ido", "0000573"))
            elements.append(("ido", "0000511"))
            context["disease_status"] = "ncit:C25269"
        # Make transmissibility a context instead of identity
        if ("ido", "0000463") in elements:
            if ("ncit", "C49508") in elements:
                context["transmissibility"] = "ncit:C49508"
                elements.remove(("ido", "0000463"))
                elements.remove(("ncit", "C49508"))
            elif ("ncit", "C171549") in elements:
                context["transmissibility"] = "ncit:C171549"
                elements.remove(("ido", "0000463"))
                elements.remove(("ncit", "C171549"))

        identifiers = dict(elements)

    if model_id:
        identifiers["biomodels.species"] = f"{model_id}:{species_id}"
    concept = Concept(
        name=species_name or species_id,
        display_name=species_name,
        identifiers=identifiers,
        # TODO how to handle multiple properties? can we extend context to allow lists?
        context=context,
        units=units,
    )
    concept = grounding_normalize(concept)
    return concept


def _get_copasi_identifiers(
    annotation_tree: etree, xpath: str
) -> Dict[str, str]:
    # Use COPASI_IS or COPASI_IS_VERSION_OF for xpath depending on use case
    return dict(
        tuple(el.attrib[RESOURCE_KEY].split(":")[-2:])
        for el in annotation_tree.xpath(xpath, namespaces=PREFIX_MAP)
    )


def _get_copasi_props(annotation_tree: etree) -> List[Tuple[str, str]]:
    return [
        tuple(el.attrib[RESOURCE_KEY].split(":")[-2:])
        for el in annotation_tree.xpath(
            COPASI_HAS_PROPERTY, namespaces=PREFIX_MAP
        )
    ]


def _extract_all_copasi_attrib(
    species_annot_etree: etree,
) -> List[Tuple[str, str]]:
    descr_tags = species_annot_etree.xpath(
        COPASI_DESCR_XPATH, namespaces=PREFIX_MAP
    )
    resources = []
    for descr_tag in descr_tags:
        for element in descr_tag.iter():
            # key = element.tag.split('}')[-1]
            key = element.tag
            attrib = element.attrib
            text = element.text.strip() if element.text is not None else ""
            if attrib and not text:
                value = attrib
            elif not attrib and text:
                value = text
            elif attrib and text:
                value = f"|{attrib}|{text}|"
            else:
                value = ""
            if value:
                assert value != "{}"
                resources.append((key, value))
    return resources
