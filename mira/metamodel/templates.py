"""
Data models for metamodel templates.

Regenerate the JSON schema by running ``python -m mira.metamodel.templates``.
"""
__all__ = [
    "Concept",
    "Parameter",
    "Initial",
    "Template",
    "Provenance",
    "ControlledConversion",
    "ControlledProduction",
    "NaturalConversion",
    "NaturalProduction",
    "NaturalDegradation",
    "GroupedControlledConversion",
    "GroupedControlledProduction",
    "TemplateModel",
    "TemplateModelComparison",
    "TemplateModelDelta",
    "RefinementClosure",
    "get_json_schema",
    "templates_equal",
    "context_refinement",
]

import json
import logging
import sys
from collections import ChainMap, defaultdict
from itertools import combinations, product, count
from pathlib import Path
from typing import (
    Callable,
    ClassVar,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Tuple,
    Union,
)

import networkx as nx
import pydantic
import sympy
from pydantic import BaseModel, Field, conint

try:
    from typing import Annotated  # py39+
except ImportError:
    from typing_extensions import Annotated


HERE = Path(__file__).parent.resolve()
SCHEMA_PATH = HERE.joinpath("schema.json")
IS_EQUAL = "equal_to"
REFINEMENT_OF = "refinement_of"
CONTROLLERS = "controllers"
CONTROLLER = "controller"
OUTCOME = "outcome"
SUBJECT = "subject"

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Config determining how keys are generated"""

    prefix_priority: List[str]
    prefix_exclusions: List[str]


DEFAULT_CONFIG = Config(
    prefix_priority=[
        "ido",
    ],
    prefix_exclusions=[
        "biomodels.species"
    ],
)


class Concept(BaseModel):
    """A concept is specified by its identifier(s), name, and - optionally -
    its context.
    """

    name: str = Field(..., description="The name of the concept.")
    identifiers: Mapping[str, str] = Field(
        default_factory=dict, description="A mapping of namespaces to identifiers."
    )
    context: Mapping[str, str] = Field(
        default_factory=dict, description="A mapping of context keys to values."
    )
    _base_name: str = pydantic.PrivateAttr(None)

    def with_context(self, do_rename=False, **context) -> "Concept":
        """Return this concept with extra context."""
        if do_rename:
            if self._base_name is None:
                self._base_name = self.name
            name = '_'.join([self._base_name] + [str(v) for v in context.values()])
        else:
            name = self.name
        concept = Concept(
            name=name,
            identifiers=self.identifiers,
            context=dict(ChainMap(context, self.context)),
        )
        concept._base_name = self._base_name
        return concept

    def get_curie(self, config: Optional[Config] = None) -> Tuple[str, str]:
        """Get the priority prefix/identifier pair for this concept."""
        if config is None:
            config = DEFAULT_CONFIG
        identifiers = {k: v for k, v in self.identifiers.items()
                       if k not in config.prefix_exclusions}

        # If ungrounded, return empty prefix and name
        if not identifiers:
            return "", self.name

        # If there are identifiers get one from the priority list
        for prefix in config.prefix_priority:
            identifier = identifiers.get(prefix)
            if identifier:
                return prefix, identifier

        # Fallback to the identifiers outside the priority list
        return sorted(identifiers.items())[0]

    def get_curie_str(self, config: Optional[Config] = None) -> str:
        """Get the priority prefix/identifier as a CURIE string."""
        return ":".join(self.get_curie(config=config))

    def get_included_identifiers(self, config: Optional[Config] = None) -> Dict[str, str]:
        config = DEFAULT_CONFIG if config is None else config
        return {k: v for k, v in self.identifiers.items() if k not in config.prefix_exclusions}

    def get_key(self, config: Optional[Config] = None):
        return (
            self.get_curie(config=config),
            tuple(sorted(self.context.items())),
        )

    def is_equal_to(self, other: "Concept", with_context: bool = False,
                    config: Config = None) -> bool:
        """Test for equality between concepts

        Parameters
        ----------
        other :
            Other Concept to test equality with
        with_context :
            If True, do not consider the two Concepts equal unless they also
            have exactly the same context. If there is no context,
            ``with_context`` has no effect.
        config :
            Configuration defining priority and exclusion for identifiers.

        Returns
        -------
        :
            True if ``other`` is the same Concept as this one
        """
        if not isinstance(other, Concept):
            return False

        # With context
        if with_context:
            # Check that the same keys appear in both
            if set(self.context.keys()) != set(other.context.keys()):
                return False

            # Check that the values are the same
            for k1, v1 in self.context.items():
                if v1 != other.context[k1]:
                    return False

        # Check that they are grounded to the same identifier
        return self.get_curie(config=config) == other.get_curie(config=config)

    def refinement_of(
        self,
        other: "Concept",
        refinement_func: Callable[[str, str], bool],
        with_context: bool = False,
        config: Config = None,
    ) -> bool:
        """Check if this Concept is a more detailed version of another

        Parameters
        ----------
        other :
            The other Concept to compare with. Assumed to be less detailed.
        with_context :
            If True, also consider the context of the Concepts for the
            refinement.
        refinement_func :
            A function that given a source/more detailed entity and a
            target/less detailed entity checks if they are in a child-parent and
            returns a boolean.
        config :
            Configuration defining priority and exclusion for identifiers.

        Returns
        -------
        :
            True if this Concept is a refinement of another Concept
        """
        if not isinstance(other, Concept):
            return False

        # If they have equivalent identity, we allow as possible refinement
        if self.is_equal_to(other, with_context=False):
            ontological_refinement = True
        # Otherwise we assume refinement is not possible, except if both
        # are grounded, and there is a refinement relationship per
        # a refinement function between them
        else:
            ontological_refinement = False
            # Check if this concept is a child term to other?
            this_prefix, this_id = self.get_curie(config=config)
            other_prefix, other_id = other.get_curie(config=config)
            if this_prefix and other_prefix:
                # Check if other is a parent of this concept
                this_curie = f"{this_prefix}:{this_id}"
                other_curie = f"{other_prefix}:{other_id}"
                ontological_refinement = refinement_func(this_curie, other_curie)

        contextual_refinement = True
        if with_context:
            contextual_refinement = \
                context_refinement(self.context, other.context)

        return ontological_refinement and contextual_refinement


class Parameter(Concept):
    """A Parameter is a special type of Concept that carries a value."""
    value: float = Field(
        default_factory=None, description="Value of the parameter."
    )


class SympyExprStr(sympy.Expr):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if isinstance(v, cls):
            return v
        return cls(v)

    @classmethod
    def __modify_schema__(cls, field_schema):
        field_schema.update(type="string", example="2*x")

    def __str__(self):
        return super().__str__()[len(self.__class__.__name__)+1:-1]

    def __repr__(self):
        return str(self)


class Template(BaseModel):
    """The Template is a parent class for model processes"""

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {
            SympyExprStr: lambda e: str(e),
        }
        json_decoders = {
            SympyExprStr: lambda e: sympy.parse_expr(e)
        }

    rate_law: Optional[SympyExprStr] = Field(default=None)

    @classmethod
    def from_json(cls, data, rate_symbols=None) -> "Template":
        # We make sure to use data such that it's not modified in place,
        # e.g., we don't use pop or overwrite items, otherwise this function
        # would have unintended side effects.

        # First, we need to figure out the template class based on the type
        # entry in the data
        stmt_cls = getattr(sys.modules[__name__], data['type'])

        # In order to correctly parse the rate, if any, we need to have access
        # to symbols representing parameters, these are passed in from
        # outside, typically the template model level.
        rate_str = data.get('rate_law')
        if rate_str:
            rate = sympy.parse_expr(rate_str, local_dict=rate_symbols)
        else:
            rate = None
        return stmt_cls(**{k: v for k, v in data.items()
                           if k not in {'rate_law', 'type'}},
                        rate_law=rate)

    def is_equal_to(self, other: "Template", with_context: bool = False,
                    config: Config = None) -> bool:
        """Check if this template is equal to another template

        Parameters
        ----------
        other :
            The other template to check for equality with this one with
        with_context :
            If True, the contexts are taken into account when checking for
            equality. Default: False.
        config :
            Configuration defining priority and exclusion for identifiers.

        Returns
        -------
        :
            True if the other Template is equal to this Template
        """
        if not isinstance(other, Template):
            return False
        return templates_equal(self, other, with_context=with_context,
                               config=config)

    def refinement_of(
        self,
        other: "Template",
        refinement_func: Callable[[str, str], bool],
        with_context: bool = False,
        config: Config = None,
    ) -> bool:
        """Check if this template is a more detailed version of another

        Parameters
        ----------
        other :
            The other template to compare with. Is assumed to be less
            detailed than this template.
        with_context :
            If True, also consider the context of Templates' Concepts for the
            refinement.
        refinement_func :
            A function that given a source/more detailed entity and a
            target/less detailed entity checks if they are in a child-parent
            relationship and returns a boolean.

        Returns
        -------
        :
            True if this Template is a refinement of the other Template.
        """
        if not isinstance(other, Template):
            return False

        compatibilities = {
            ('ControlledConversion', 'NaturalConversion'),
            ('GroupedControlledConversion', 'NaturalConversion'),
            ('GroupedControlledConversion', 'ControlledConversion')
        }

        if self.type != other.type and \
                (self.type, other.type) not in compatibilities:
            return False

        other_by_role = other.get_concepts_by_role()
        for role, value in self.get_concepts_by_role().items():
            # This is a special case to handle the list vs single controller
            # with distinct role names
            if role == 'controllers':
                if 'controllers' in other_by_role:
                    other_value = other_by_role['controllers']
                elif 'controller'in other_by_role:
                    other_value = [other_by_role['controller']]
                else:
                    other_value = None
            else:
                other_value = other_by_role.get(role)
            # This case handles less detailed other classes where a given
            # role might be missing
            if other_value is None:
                continue
            # When we are comparing concepts
            if isinstance(value, Concept):
                if not value.refinement_of(other_value,
                                           refinement_func=refinement_func,
                                           with_context=with_context,
                                           config=config):
                    return False
            # When we are comparing lists of concepts
            elif isinstance(value, list):
                if not match_concepts(value, other_value,
                                      with_context=with_context,
                                      config=config,
                                      refinement_func=refinement_func):
                    return False
        return True

    def get_concepts(self):
        """Return the concepts in this template."""
        return [getattr(self, k) for k in self.concept_keys]

    def get_concepts_by_role(self):
        """Return the concepts in this template as a dict keyed by role."""
        return {
            k: getattr(self, k) for k in self.concept_keys
        }

    def get_concept_names(self):
        """Return the concept names in this template."""
        return {c.name for c in self.get_concepts()}

    def get_interactors(self) -> List[Concept]:
        """Return the interactors in this template."""
        concepts_by_role = self.get_concepts_by_role()
        if 'controller' in concepts_by_role:
            controllers = [concepts_by_role['controller']]
        elif 'controllers' in concepts_by_role:
            controllers = concepts_by_role['controllers']
        else:
            controllers = []
        subject = concepts_by_role.get('subject')
        interactors = controllers + ([subject] if subject else [])
        return interactors

    def get_controllers(self):
        concepts_by_role = self.get_concepts_by_role()
        if 'controller' in concepts_by_role:
            controllers = [concepts_by_role['controller']]
        elif 'controllers' in concepts_by_role:
            controllers = concepts_by_role['controllers']
        else:
            controllers = []
        return controllers

    def get_interactor_rate_law(self, independent=False) -> sympy.Expr:
        """Return the rate law for the interactors in this template.

        This is the part of the rate law that is the product of the interactors
        but does not include any parameters.
        """
        rate_law = 1
        if not independent:
            for interactor in self.get_interactors():
                rate_law *= sympy.Symbol(interactor.name)
        else:
            concepts_by_role = self.get_concepts_by_role()
            subject = concepts_by_role.get('subject')
            controllers = self.get_controllers()
            rate_law *= sympy.Symbol(subject.name)
            controller_terms = 1
            for controller in controllers:
                controller_terms += sympy.Symbol(controller.name)
            rate_law *= controller_terms
        return rate_law

    def get_mass_action_rate_law(self, parameter: str, independent=False) -> sympy.Expr:
        """Return the mass action rate law for this template.

        Parameters
        ----------
        parameter :
            The parameter to use for the mass-action rate law.

        Returns
        -------
        :
            The mass action rate law for this template.
        """
        rate_law = sympy.Symbol(parameter) * \
            self.get_interactor_rate_law(independent=independent)
        return rate_law

    def get_independent_mass_action_rate_law(self, parameter: str):
        rate_law = sympy.Symbol(parameter) * \
            self.get_interactor_rate_law(independent=True)
        return rate_law

    def set_mass_action_rate_law(self, parameter, independent=False):
        """Set the rate law of this template to a mass action rate law.

        Parameters
        ----------
        parameter :
            The parameter to use for the mass-action rate.
        """
        self.rate_law = SympyExprStr(
            self.get_mass_action_rate_law(parameter, independent=independent))


class Provenance(BaseModel):
    pass


class ControlledConversion(Template):
    """Specifies a process of controlled conversion from subject to outcome,
    controlled by the controller."""

    type: Literal["ControlledConversion"] = Field("ControlledConversion", const=True)
    controller: Concept
    subject: Concept
    outcome: Concept
    provenance: List[Provenance] = Field(default_factory=list)

    concept_keys: ClassVar[List[str]] = ["controller", "subject", "outcome"]

    def with_context(self, do_rename=False, **context) -> "ControlledConversion":
        """Return a copy of this template with context added"""
        return self.__class__(
            type=self.type,
            subject=self.subject.with_context(do_rename=do_rename, **context),
            outcome=self.outcome.with_context(do_rename=do_rename, **context),
            controller=self.controller.with_context(do_rename=do_rename, **context),
            provenance=self.provenance,
            rate_law=self.rate_law,
        )

    def add_controller(self, controller: Concept) -> "GroupedControlledConversion":
        """Add an additional controller."""
        return GroupedControlledConversion(
            subject=self.subject,
            outcome=self.outcome,
            provenance=self.provenance,
            controllers=[self.controller, controller]
        )

    def get_key(self, config: Optional[Config] = None):
        return (
            self.type,
            self.subject.get_key(config=config),
            self.controller.get_key(config=config),
            self.outcome.get_key(config=config),
        )


class GroupedControlledConversion(Template):
    type: Literal["GroupedControlledConversion"] = Field("GroupedControlledConversion", const=True)
    controllers: List[Concept]
    subject: Concept
    outcome: Concept
    provenance: List[Provenance] = Field(default_factory=list)

    concept_keys: ClassVar[List[str]] = ["controllers", "subject", "outcome"]

    def with_context(self, do_rename=False, **context) -> "GroupedControlledConversion":
        """Return a copy of this template with context added"""
        return self.__class__(
            type=self.type,
            controllers=[c.with_context(do_rename, **context) for c in self.controllers],
            subject=self.subject.with_context(do_rename, **context),
            outcome=self.outcome.with_context(do_rename, **context),
            provenance=self.provenance,
            rate_law=self.rate_law,
        )

    def get_key(self, config: Optional[Config] = None):
        return (
            self.type,
            *tuple(
                c.get_key(config=config)
                for c in sorted(self.controllers, key=lambda c: c.get_key(config=config))
            ),
            self.subject.get_key(config=config),
            self.outcome.get_key(config=config),
        )

    def get_concepts(self):
        """Return the concepts in this template."""
        return self.controllers + [self.subject, self.outcome]

    def add_controller(self, controller: Concept) -> "GroupedControlledConversion":
        """Add an additional controller."""
        return GroupedControlledConversion(
            subject=self.subject,
            outcome=self.outcome,
            provenance=self.provenance,
            controllers=[*self.controllers, controller]
        )


class GroupedControlledProduction(Template):
    """Specifies a process of production controlled by several controllers"""

    type: Literal["GroupedControlledProduction"] = Field("GroupedControlledProduction", const=True)
    controllers: List[Concept]
    outcome: Concept
    provenance: List[Provenance] = Field(default_factory=list)

    concept_keys: ClassVar[List[str]] = ["controllers", "outcome"]

    def get_key(self, config: Optional[Config] = None):
        return (
            self.type,
            *tuple(
                c.get_key(config=config)
                for c in sorted(self.controllers, key=lambda c: c.get_key(config=config))
            ),
            self.outcome.get_key(config=config),
        )

    def get_concepts(self):
        """Return a list of the concepts in this template"""
        return self.controllers + [self.outcome]

    def add_controller(self, controller: Concept) -> "GroupedControlledProduction":
        """Add an additional controller."""
        return GroupedControlledProduction(
            outcome=self.outcome,
            provenance=self.provenance,
            controllers=[*self.controllers, controller]
        )

class ControlledProduction(Template):
    """Specifies a process of production controlled by one controller"""

    type: Literal["ControlledProduction"] = Field("ControlledProduction", const=True)
    controller: Concept
    outcome: Concept
    provenance: List[Provenance] = Field(default_factory=list)

    concept_keys: ClassVar[List[str]] = ["controller", "outcome"]

    def get_key(self, config: Optional[Config] = None):
        return (
            self.type,
            self.controller.get_key(config=config),
            self.outcome.get_key(config=config),
        )

    def add_controller(self, controller: Concept) -> "GroupedControlledProduction":
        """Add an additional controller."""
        return GroupedControlledProduction(
            outcome=self.outcome,
            provenance=self.provenance,
            controllers=[self.controller, controller]
        )


class NaturalConversion(Template):
    """Specifies a process of natural conversion from subject to outcome"""

    type: Literal["NaturalConversion"] = Field("NaturalConversion", const=True)
    subject: Concept
    outcome: Concept
    provenance: List[Provenance] = Field(default_factory=list)

    concept_keys: ClassVar[List[str]] = ["subject", "outcome"]

    def with_context(self, do_rename=False, **context) -> "NaturalConversion":
        """Return a copy of this template with context added"""
        return self.__class__(
            type=self.type,
            subject=self.subject.with_context(do_rename=do_rename, **context),
            outcome=self.outcome.with_context(do_rename=do_rename, **context),
            provenance=self.provenance,
            rate_law=self.rate_law,
        )

    def get_key(self, config: Optional[Config] = None):
        return (
            self.type,
            self.subject.get_key(config=config),
            self.outcome.get_key(config=config),
        )


class NaturalProduction(Template):
    """A template for the production of a species at a constant rate."""

    type: Literal["NaturalProduction"] = Field("NaturalProduction", const=True)
    outcome: Concept
    provenance: List[Provenance] = Field(default_factory=list)

    concept_keys: ClassVar[List[str]] = ["outcome"]

    def get_key(self, config: Optional[Config] = None):
        return (
            self.type,
            self.outcome.get_key(config=config),
        )


class NaturalDegradation(Template):
    """A template for the degradataion of a species at a proportional rate to its amount."""

    type: Literal["NaturalDegradation"] = Field("NaturalDegradation", const=True)
    subject: Concept
    provenance: List[Provenance] = Field(default_factory=list)

    concept_keys: ClassVar[List[str]] = ["subject"]

    def get_key(self, config: Optional[Config] = None):
        return (
            self.type,
            self.subject.get_key(config=config),
        )


def get_json_schema():
    """Get the JSON schema for MIRA."""
    rv = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "https://raw.githubusercontent.com/indralab/mira/main/mira/metamodel/schema.json",
    }
    rv.update(
        pydantic.schema.schema(
            [
                Concept,
                Template,
                *Template.__subclasses__(),
            ],
            title="MIRA Metamodel Template Schema",
            description="MIRA metamodel templates give a high-level abstraction of modeling appropriate for many domains.",
        )
    )
    return rv


def templates_equal(templ: Template, other_templ: Template, with_context: bool,
                    config: Config) -> bool:
    """Check if two Template objects are equal

    Parameters
    ----------
    templ :
        A template to compare.
    other_templ :
        The other template to compare.
    with_context :
        If True, also check the contexts of the contained Concepts of the
        Template.
    config :
        Configuration defining priority and exclusion for identifiers.

    Returns
    -------
    :
        True if the two Template objects are equal.
    """
    if templ.type != other_templ.type:
        return False

    other_by_role = other_templ.get_concepts_by_role()
    for role, value in templ.get_concepts_by_role().items():
        other_value = other_by_role.get(role)
        if isinstance(value, Concept):
            if not value.is_equal_to(other_value, with_context=with_context,
                                     config=config):
                return False
        elif isinstance(value, list):
            if len(value) != len(other_value):
                return False

            if not match_concepts(value, other_value,
                                  with_context=with_context,
                                  config=config):
                return False
    return True


def match_concepts(self_concepts, other_concepts, with_context=True,
                   config=None, refinement_func=None):
    """Return true if there is an exact match between two lists of concepts."""
    # First build a bipartite graph of matches
    G = nx.Graph()
    for (self_idx, self_concept), (other_idx, other_concept) in \
            product(enumerate(self_concepts), enumerate(other_concepts)):
        if refinement_func:
            res = self_concept.refinement_of(other_concept,
                                             with_context=with_context,
                                             refinement_func=refinement_func,
                                             config=config)
        else:
            res = self_concept.is_equal_to(other_concept,
                                           with_context=with_context,
                                           config=config)
        if res:
            G.add_edge('S%d' % self_idx, 'O%d' % other_idx)
    # Then find a maximal matching in the bipartite graph
    match = nx.algorithms.max_weight_matching(G)
    # If all the other concepts are covered, this is considered a match.
    # The reason for checking this as a condition is that this works for
    # both the equality case where the two lists have the same length, and
    # the refinement case where we want to find a match/refinement for
    # each of the concepts in the other list.
    return len(match) == len(other_concepts)


def context_refinement(refined_context, other_context) -> bool:
    """Check if one Concept's context is a refinement of another Concept's

    Parameters
    ----------
    refined_context :
        The assumed *more* detailed context
    other_context :
        The assumed *less* detailed context

    Returns
    -------
    :
        True if the Concept `refined_concept` truly is strictly more detailed
        than `other_concept`
    """
    # 1. True if no context for both
    if not refined_context and not other_context:
        return True
    # 2. True if refined concept has context and the other one not
    elif refined_context and not other_context:
        return True
    # 3. False if refined concept does not have context and the other does
    elif not refined_context and other_context:
        return False
    # 4. Both have context, in which case we need to make sure there is no
    # explicit difference for any key/value pair that exists in other. This
    # means that the refined context can have additional keys/values, or
    # the two contexts can be exactly equal
    else:
        for other_key, other_val in other_context.items():
            if refined_context.get(other_key) != other_val:
                return False
    return True


# Needed for proper parsing by FastAPI
SpecifiedTemplate = Annotated[
    Union[
        NaturalConversion,
        ControlledConversion,
        NaturalDegradation,
        NaturalProduction,
        GroupedControlledConversion,
        GroupedControlledProduction,
    ],
    Field(description="Any child class of a Template", discriminator="type"),
]


class Initial(BaseModel):
    """An initial condition."""

    concept: Concept
    value: float


class DataNode(BaseModel):
    """A node in a ModelComparisonGraphdata"""

    node_type: Literal["template", "concept"]
    model_id: conint(ge=0, strict=True)


class TemplateNode(DataNode):
    """A node in a ModelComparisonGraphdata representing a Template"""

    type: str
    rate_law: Optional[SympyExprStr] = \
        Field(default=None, description="The rate law of this template")
    initials: Optional[Mapping[str, Initial]] = \
        Field(default=None, description="The initial conditions associated "
                                        "with the rate law for this template")
    provenance: List[Provenance] = Field(default_factory=list)


class ConceptNode(Concept, DataNode):
    """A node in a ModelComparisonGraphdata representing a Concept"""

    curie: str


DataNodeKey = Tuple[str, ...]


class DataEdge(BaseModel):
    """An edge in a ModelComparisonGraphdata"""

    source_id: DataNodeKey
    target_id: DataNodeKey


class InterModelEdge(DataEdge):
    role: Literal["refinement_of", "is_equal"]


class IntraModelEdge(DataEdge):
    role: Literal["subject", "outcome", "controller"]


class TemplateModel(BaseModel):
    """A template model."""

    templates: List[SpecifiedTemplate] = Field(
        ..., description="A list of any child class of Templates"
    )
    parameters: Mapping[str, Parameter] = \
        Field(default_factory=dict,
              description="A dict of parameter values where keys correspond "
                          "to how the parameter appears in rate laws.")
    initials: Mapping[str, Initial] = \
        Field(default_factory=dict,
              description="A dict of initial condition values where keys"
                          "correspond to concept names they apply to.")

    class Config:
        json_encoders = {
            SympyExprStr: lambda e: str(e),
        }
        json_decoders = {
            SympyExprStr: lambda e: sympy.parse_expr(e)
        }

    def get_parameters_from_rate_law(self, rate_law):
        """Given a rate law, find its elements that are model parameters.

        Rate laws consist of some combination of participants, rate parameters
        and potentially other factors. This function finds those elements of
        rate laws that are rate parameters.
        """
        if not rate_law:
            return set()
        params = set()
        if isinstance(rate_law, sympy.Symbol):
            if rate_law.name in self.parameters:
                params.add(rate_law.name)
        else:
            assert isinstance(rate_law, sympy.Expr), (rate_law, type(rate_law))
            for arg in rate_law.args:
                params |= self.get_parameters_from_rate_law(arg)
        return params

    def update_parameters(self, parameter_dict):
        for k, v in parameter_dict.items():
            if k in self.parameters:
                self.parameters[k].value = v
            else:
                self.parameters[k] = Parameter(name=k, value=v)

    @classmethod
    def from_json(cls, data) -> "TemplateModel":
        local_symbols = {p: sympy.Symbol(p) for p in data.get('parameters', [])}
        for template_dict in data.get('templates', []):
            # We need to figure out the template class based on the type
            # entry in the data
            template_cls = getattr(sys.modules[__name__], template_dict['type'])
            for concept_key in template_cls.concept_keys:
                # Note the special handling here for list-like vs single
                # concepts
                concept_data = template_dict.get(concept_key)
                if concept_data:
                    if not isinstance(concept_data, list):
                        concept_data = [concept_data]
                    for concept_dict in concept_data:
                        if concept_dict.get('name'):
                            local_symbols[concept_dict.get('name')] = \
                                sympy.Symbol(concept_dict.get('name'))
        # We can now use these symbols to deserialize rate laws
        templates = [Template.from_json(template, rate_symbols=local_symbols)
                     for template in data["templates"]]

        #: A lookup from concept name in the model to the full
        #: concept object to be used for preparing initial values
        concepts = {
            concept.name: concept
            for template in templates
            for concept in template.get_concepts()
        }

        initials = {
            name: (
                Initial(
                    concept=concepts[name],
                    value=value,
                )
                # If the data is just a float, upgrade it to
                # a :class:`Initial` instance
                if isinstance(value, float)
                # If the data is not a float, assume it's JSON
                # for a :class:`Initial` instance
                else value
            )
            for name, value in data.get('initials', {}).items()
        }

        return cls(templates=templates,
                   parameters=data.get('parameters', {}),
                   initials=initials)

    def generate_model_graph(self) -> nx.DiGraph:
        graph = nx.DiGraph()
        for template in self.templates:

            # Add node for template itself
            node_id = get_template_graph_key(template)
            graph.add_node(
                node_id,
                type=template.type,
                template_key=template.get_key(),
                label=template.type,
                color="orange",
                shape="record",
            )

            # Add in/outgoing nodes for the concepts of this template
            for role, concepts in template.get_concepts_by_role().items():
                for concept in concepts if isinstance(concepts, list) else [concepts]:
                    # Note: this includes the node's name as well as its
                    # grounding
                    concept_key = get_concept_graph_key(concept)
                    # Note that this doesn't include the concept's name
                    # in the key
                    concept_identity_key = concept.get_key()
                    context_str = "\n".join(
                        f"{k}-{v}" for k, v in concept.context.items()
                    )
                    context_str = "\n" + context_str if context_str else ""
                    if concept.get_included_identifiers():
                        label = (
                            f"{concept.name}\n({concept.get_curie_str()})"
                            f"{context_str}"
                        )
                    else:
                        label = f"{concept.name}\n(ungrounded){context_str}"
                    graph.add_node(
                        concept_key,
                        label=label,
                        color="orange",
                        concept_identity_key=concept_identity_key,
                    )
                    role_label = "controller" if role == "controllers" \
                        else role
                    if role_label in {"controller", "subject"}:
                        source, target = concept_key, node_id
                    else:
                        source, target = node_id, concept_key
                    graph.add_edge(source, target, label=role_label)

        return graph

    def draw_graph(
        self, path: str, prog: str = "dot", args: str = "", format: Optional[str] = None
    ):
        """Draw a pygraphviz graph of the TemplateModel

        Parameters
        ----------
        path :
            The path to the output file
        prog :
            The graphviz layout program to use, such as "dot", "neato", etc.
        format :
            Set the file format explicitly
        args :
            Additional arguments to pass to the graphviz bash program as a
            string. Example: args="-Nshape=box -Edir=forward -Ecolor=red"
        """
        # draw graph
        graph = self.generate_model_graph()
        agraph = nx.nx_agraph.to_agraph(graph)
        agraph.draw(path, format=format, prog=prog, args=args)

    def graph_as_json(self) -> Dict:
        """Serialize the TemaplateModel graph as node-link data"""
        graph = self.generate_model_graph()
        return nx.node_link_data(graph)

    def print_params_table(self):
        import tabulate
        contexts = set()
        for key, param in self.parameters.items():
            contexts |= set(param.context.keys())

        header = ['name', 'identifier'] + sorted(contexts)
        rows = [header]
        for key, param in self.parameters.items():
            identifier_curie = ':'.join(list(param.identifiers.items())[0])
            context_entries = [param.context.get(context)
                               for context in sorted(contexts)]
            rows.append([key, identifier_curie] + context_entries)

        print(tabulate.tabulate(rows, headers='firstrow'))

    def get_concepts_map(self):
        """
        Get a mapping from concept keys to concepts that
        appear in this template models' templates.
        """
        return {concept.get_key(): concept for concept in _iter_concepts(self)}

    def get_concepts_by_name(self, name: str) -> List[Concept]:
        """Get a list of all concepts that have the given name.

        .. warning::

            this could give duplicates if there are nodes with
            compositional grounding
        """
        name = name.casefold()
        return [
            concept
            for template in self.templates
            for concept in template.get_concepts()
            if concept.name.casefold() == name
        ]

    def extend(self, template_model: "TemplateModel",
               parameter_mapping: Optional[Mapping[str, Parameter]] = None,
               initial_mapping: Optional[Mapping[str, Initial]] = None):
        """Extend this template model with another template model."""
        model = self
        for template in template_model.templates:
            model = model.add_template(template,
                                       parameter_mapping=parameter_mapping,
                                       initial_mapping=initial_mapping)
        return model

    def add_template(
            self,
            template: Template,
            parameter_mapping: Optional[Mapping[str, Parameter]] = None,
            initial_mapping: Optional[Mapping[str, Initial]] = None,
    ) -> "TemplateModel":
        """Add a template to the model

        Parameters
        ----------
        template :
            The template to add
        parameter_mapping :
            A mapping from parameter names in the template to Parameter
            instances in the model.
        initial_mapping :
            A mapping from concept names in the template to Initial
            instances in the model

        Returns
        -------
        :
            A new model with the additional template
        """
        # todo: handle adding parameters and initials
        if parameter_mapping is None and initial_mapping is None:
            return TemplateModel(templates=self.templates + [template],
                                 parameters=self.parameters,
                                 initials=self.initials)
        elif parameter_mapping is None:
            initials = (self.initials or {})
            initials.update(initial_mapping or {})
            return TemplateModel(
                templates=self.templates + [template],
                initials=initials,
                parameters=self.parameters,
            )
        elif initial_mapping is None:
            parameters = (self.parameters or {})
            parameters.update(parameter_mapping or {})
            return TemplateModel(
                templates=self.templates + [template],
                parameters=parameters,
                initials=self.initials,
            )
        else:
            initials = (self.initials or {})
            initials = initials.update(initial_mapping or {})
            parameters = (self.parameters or {})
            parameters.update(parameter_mapping or {})
            return TemplateModel(
                templates=self.templates + [template],
                parameters=parameters,
                initials=initials,
            )

    def add_transition(
        self,
        subject_concept: Concept,
        outcome_concept: Concept,
        parameter: Optional[Parameter] = None,
    ) -> "TemplateModel":
        """Add a NaturalConversion between a source and an outcome.

        We assume mass action kinetics with a single parameter.
        """
        template = NaturalConversion(
            subject=subject_concept,
            outcome=outcome_concept,
        )
        if parameter:
            template.set_mass_action_rate_law(parameter.name)
        pm = {parameter.name: parameter} if parameter else None
        return self.add_template(template, parameter_mapping=pm)


def _iter_concepts(template_model: TemplateModel):
    for template in template_model.templates:
        if isinstance(template, ControlledConversion):
            yield from (template.subject, template.outcome, template.controller)
        elif isinstance(template, NaturalConversion):
            yield from (template.subject, template.outcome)
        elif isinstance(template, GroupedControlledConversion):
            yield from template.controllers
            yield from (template.subject, template.outcome)
        elif isinstance(template, NaturalDegradation):
            yield template.subject
        elif isinstance(template, NaturalProduction):
            yield template.outcome
        else:
            raise TypeError(f"could not handle template: {template}")


class ModelComparisonGraphdata(BaseModel):
    """A data structure holding a graph representation of a TemplateModel"""
    class Config:
        arbitrary_types_allowed = True
        json_encoders = {
            SympyExprStr: lambda e: str(e),
        }
        json_decoders = {
            SympyExprStr: lambda e: sympy.parse_expr(e),
            Template: lambda t: Template.from_json(data=t),
        }

    template_models: Dict[int, TemplateModel] = Field(
        ..., description="A mapping of template model keys to template models"
    )
    concept_nodes: Dict[int, Dict[int, Concept]] = Field(
        default_factory=list,
        description="A mapping of model identifiers to a mapping of node "
        "identifiers to nodes. Node identifiers have the structure of 'mXnY' "
        "where X is the model id and Y is the node id within the model.",
    )
    template_nodes: Dict[int, Dict[int, Template]] = Field(
        default_factory=list,
        description="A mapping of model identifiers to a mapping of node "
        "identifiers to nodes. Node identifiers have the structure of 'mXnY' "
        "where X is the model id and Y is the node id within the model.",
    )
    # nodes are tuples of (model id, node id) for look
    inter_model_edges: List[Tuple[Tuple[int, int], Tuple[int, int], str]] = \
        Field(
        default_factory=list,
        description="List of edges. Each edge is a tuple of"
        "(source node lookup, target node lookup, role) where role describes "
        "if the edge is a refinement of or equal to another node in another "
        "model (inter model edge). The edges are considered directed for "
        "refinements and undirected for equalities. The node lookup is a "
        "tuple of (model id, node id) that defines the lookup of the node "
        "in the nodes mapping.",
    )
    intra_model_edges: List[Tuple[Tuple[int, int], Tuple[int, int], str]] = Field(
        default_factory=list,
        description="List of edges. Each edge is a tuple of"
        "(source node lookup, target node lookup, role) where role describes "
        "if the edge incoming to, outgoing from or controls a "
        "template/process in the same model (intra model edge). The edges "
        "are considered directed. The node lookup is a tuple of "
        "(model id, node id) that defines the lookup of the node in the "
        "nodes mapping.",
    )

    def get_similarity_score(self, model1_id: int, model2_id: int) -> float:
        """Get the similarity score of the model comparison"""

        # Get all concept nodes for each model
        model1_concept_nodes = set()
        for node_id, node in self.concept_nodes[model1_id].items():
            model1_concept_nodes.add((model1_id, node_id))
        model2_concept_nodes = set()
        for node_id, node in self.concept_nodes[model2_id].items():
            model2_concept_nodes.add((model2_id, node_id))

        # Check which model has the most nodes
        n_nodes1 = len(model1_concept_nodes)
        n_nodes2 = len(model2_concept_nodes)

        # Set model 1 to be the model with the most nodes
        if n_nodes2 > n_nodes1:
            # Witch the sets
            model1_concept_nodes, model2_concept_nodes = \
                model2_concept_nodes, model1_concept_nodes
            # Switch the number of nodes
            n_nodes2, n_nodes1 = n_nodes1, n_nodes2
            # Switch the model ids
            model1_id, model2_id = model2_id, model1_id

        # Create an index of all the edges between the two models
        index = defaultdict(lambda: defaultdict(set))
        for t in (IS_EQUAL, REFINEMENT_OF):
            for (msource_id, source_id), (mtarget_id, target_id), e_type in \
                    self.inter_model_edges:
                source_tuple = (msource_id, source_id)
                target_tuple = (mtarget_id, target_id)
                if e_type != t:
                    continue

                # Add model1 -> model2 edge
                if msource_id == model1_id and mtarget_id == model2_id:
                    index[t][source_tuple].add(target_tuple)
                # Add model2 -> model1 edge
                if msource_id == model2_id and mtarget_id == model1_id:
                    index[t][target_tuple].add(source_tuple)

        score = 0
        for model1_node_json in model1_concept_nodes:
            if model1_node_json in index[IS_EQUAL]:
                # todo: fix this check
                score += 1
            elif model1_node_json in index[REFINEMENT_OF]:
                score += 0.5

        # Todo: Come up with a better metric?
        concept_similarity_score = score / n_nodes1

        return concept_similarity_score

    def get_similarity_scores(self):
        """Get the similarity scores for all model comparisons"""
        scores = []
        for i, j in combinations(range(len(self.template_models)), 2):
            scores.append({
                'models': (i, j),
                'score': self.get_similarity_score(i, j)
            })
        return scores

    @classmethod
    def from_template_models(
            cls,
            template_models: List[TemplateModel],
            refinement_func: Callable[[str, str], bool]
    ) -> "ModelComparisonGraphdata":
        return TemplateModelComparison(
            template_models, refinement_func
        ).model_comparison


class TemplateModelComparison:
    """Compares TemplateModels in a graph friendly structure"""
    model_comparison: ModelComparisonGraphdata

    def __init__(
        self,
        template_models: List[TemplateModel],
        refinement_func: Callable[[str, str], bool]
    ):
        # Todo: Add more identifiable ID to template model than index?
        if len(template_models) < 2:
            raise ValueError("Need at least two models to make comparison")
        self.template_node_lookup: Dict[Tuple, Template] = {}
        self.concept_node_lookup: Dict[Tuple, Concept] = {}
        self.intra_model_edges: List[Tuple[Tuple, Tuple, str]] = []
        self.inter_model_edges: List[Tuple[Tuple, Tuple, str]] = []
        self.refinement_func = refinement_func
        self.template_models: Dict[int, TemplateModel] = {
            ix: tm for ix, tm in enumerate(iterable=template_models)
        }
        self.compare_models()

    def _add_concept_nodes_edges(
            self,
            template_node_id: Tuple,
            role: str,
            concept: Union[Concept, List[Concept]]):
        model_id = template_node_id[0]
        # Add one or several concept nodes with their template-concept edges
        if isinstance(concept, Concept):
            # Just need some hashable id for the concept and then translate
            # it to an integer
            concept_node_id = (model_id,) + get_concept_graph_key(concept)
            if concept_node_id not in self.concept_node_lookup:
                self.concept_node_lookup[concept_node_id] = concept

            # Add edges for subjects, controllers and outcomes
            if role in [CONTROLLER, CONTROLLERS, SUBJECT]:
                self.intra_model_edges.append(
                    (concept_node_id, template_node_id, role)
                )
            elif role == OUTCOME:
                self.intra_model_edges.append(
                    (template_node_id, concept_node_id, role)
                )
            else:
                raise ValueError(f"Invalid role {role}")
        elif isinstance(concept, list):
            for conc in concept:
                self._add_concept_nodes_edges(
                    template_node_id, role, conc
                )
        else:
            raise TypeError(f"Invalid concept type {type(concept)}")

    def _add_template_model(
            self, model_id: int, template_model: TemplateModel
    ):
        # Create the graph data for this template model
        for template in template_model.templates:
            template_node_id = (model_id, ) + get_template_graph_key(template)
            if template_node_id not in self.template_node_lookup:
                self.template_node_lookup[template_node_id] = template

            # Add concept nodes and intra model edges
            for role, concept in template.get_concepts_by_role().items():
                self._add_concept_nodes_edges(template_node_id, role, concept)

    def _add_inter_model_edges(
        self,
        node_id1: Tuple[str, ...],
        data_node1: Union[Concept, Template],
        node_id2: Tuple[str, ...],
        data_node2: Union[Concept, Template],
    ):
        if data_node1.is_equal_to(data_node2, with_context=True):
            # Add equality edge
            self.inter_model_edges.append(
                (node_id1, node_id2, "is_equal")
            )
        elif data_node1.refinement_of(data_node2, self.refinement_func, with_context=True):
            self.inter_model_edges.append(
                (node_id1, node_id2, "refinement_of")
            )
        elif data_node2.refinement_of(data_node1, self.refinement_func, with_context=True):
            self.inter_model_edges.append(
                (node_id2, node_id1, "refinement_of")
            )

    def compare_models(self):
        """Compare TemplateModels and return a graph of the differences"""
        for model_id, template_model in self.template_models.items():
            self._add_template_model(model_id, template_model)

        # Create inter model edges, i.e refinements and equalities
        for (node_id1, data_node1), (node_id2, data_node2) in combinations(
                self.template_node_lookup.items(), r=2):
            if node_id1[:2] == node_id2[:2]:
                continue
            self._add_inter_model_edges(node_id1, data_node1,
                                        node_id2, data_node2)

        # Create inter model edges, i.e refinements and equalities
        for (node_id1, data_node1), (node_id2, data_node2) in combinations(
                self.concept_node_lookup.items(), r=2):
            if node_id1[:2] == node_id2[:2]:
                continue
            self._add_inter_model_edges(node_id1, data_node1,
                                        node_id2, data_node2)

        concept_nodes = defaultdict(dict)
        template_nodes = defaultdict(dict)
        model_node_counters = {}
        old_new_map = {}
        for old_node_id, node in self.template_node_lookup.items():
            m_id = old_node_id[0]

            # Restart node counter for new models
            if m_id not in model_node_counters:
                model_node_counter = count()
                model_node_counters[m_id] = model_node_counter
            else:
                model_node_counter = model_node_counters[m_id]

            node_id = next(model_node_counter)
            old_new_map[old_node_id] = (m_id, node_id)
            template_nodes[m_id][node_id] = node

        for old_node_id, node in self.concept_node_lookup.items():
            m_id = old_node_id[0]

            # Restart node counter for new models
            if m_id not in model_node_counters:
                model_node_counter = count()
                model_node_counters[m_id] = model_node_counter
            else:
                model_node_counter = model_node_counters[m_id]

            node_id = next(model_node_counter)
            old_new_map[old_node_id] = (m_id, node_id)
            concept_nodes[m_id][node_id] = node

            # todo: consider doing nested arrays instead of nested mappings
            #  for both nodes and models
            # nodes: [
            #           [{node}, ...],
            #           [{node}, ...],
            #       ]

        # translate old node ids to new node ids in the edges
        inter_model_edges = [
            (old_new_map[old_node_id1], old_new_map[old_node_id2], edge_type)
            for old_node_id1, old_node_id2, edge_type in self.inter_model_edges
        ]
        intra_model_edges = [
            (old_new_map[old_node_id1], old_new_map[old_node_id2], edge_type)
            for old_node_id1, old_node_id2, edge_type in self.intra_model_edges
        ]
        self.model_comparison = ModelComparisonGraphdata(
            template_models=self.template_models,
            template_nodes=template_nodes,
            concept_nodes=concept_nodes,
            inter_model_edges=inter_model_edges,
            intra_model_edges=intra_model_edges
        )


class TemplateModelDelta:
    """Defines the differences between TemplateModels as a networkx graph"""

    def __init__(
        self,
        template_model1: TemplateModel,
        template_model2: TemplateModel,
        refinement_function: Callable[[str, str], bool],
        tag1: str = "1",
        tag2: str = "2",
        tag1_color: str = "orange",
        tag2_color: str = "blue",
        merge_color: str = "red",
    ):
        self.refinement_func = refinement_function
        self.template_model1 = template_model1
        self.templ1_graph = template_model1.generate_model_graph()
        self.tag1 = tag1
        self.tag1_color = tag1_color
        self.template_model2 = template_model2
        self.templ2_graph = template_model2.generate_model_graph()
        self.tag2 = tag2
        self.tag2_color = tag2_color
        self.merge_color = merge_color
        self.comparison_graph = nx.DiGraph()
        self.comparison_graph.graph["rankdir"] = "LR"  # transposed node tables
        self._assemble_comparison()

    def _add_node(self, template: Template, tag: str):
        # Get a unique identifier for node
        node_id = (*get_template_graph_key(template), tag)
        self.comparison_graph.add_node(
            node_id,
            type=template.type,
            template_key=template.get_key(),
            label=template.type,
            color=self.tag1_color if tag == self.tag1 else self.tag2_color,
            shape="record",
        )
        return node_id

    def _add_edge(
        self,
        source: Template,
        source_tag: str,
        target: Template,
        target_tag: str,
        edge_type: Literal["refinement_of", "is_equal"],
    ):
        n1_id = self._add_node(source, tag=source_tag)
        n2_id = self._add_node(target, tag=target_tag)

        if edge_type == "refinement_of":
            # source is a refinement of target
            self.comparison_graph.add_edge(n1_id, n2_id, label=edge_type,
                                           color="red", weight=2)
        else:
            # is_equal: add edges both ways
            self.comparison_graph.add_edge(n1_id, n2_id, label=edge_type,
                                           color="red", weight=2)
            self.comparison_graph.add_edge(n2_id, n1_id, label=edge_type,
                                           color="red", weight=2)

    def _add_graphs(self):
        # Add the graphs together
        nodes_to_add = []
        template_node_ids = set()
        for node, node_data in self.templ1_graph.nodes(data=True):
            # If Template node, append tag to node id
            if "template_key" in node_data:
                # NOTE: if we want to merge Template nodes skip appending
                # the tag to the tuple
                node_id = (*node, self.tag1)
                template_node_ids.add(node)
            else:
                # Assumed to be a Concept node
                node_id = node
            node_data["color"] = self.tag1_color
            nodes_to_add.append((node_id, {"tags": {self.tag1}, **node_data}))

        self.comparison_graph.add_nodes_from(nodes_to_add)

        model1_identity_keys = {
            data['concept_identity_key']: node for node, data
            in self.templ1_graph.nodes(data=True)
            if 'concept_identity_key' in data
        }

        to_contract = set()

        # For the other template, add nodes that are missing, update data
        # for the ones that are already in
        for node, node_data in self.templ2_graph.nodes(data=True):
            # NOTE: if we want to merge Template nodes skip appending
            # the tag to the tuple
            if "template_key" in node_data:
                node_id = (*node, self.tag2)
                template_node_ids.add(node)
                node_data["tags"] = {self.tag2}
                node_data["color"] = self.tag2_color
                self.comparison_graph.add_node(node_id, **node_data)
            else:
                # There is an exact match for this node so we don't need
                # to add it
                if node in self.comparison_graph.nodes:
                    # If node already exists, add to tags and update color
                    self.comparison_graph.nodes[node]["tags"].add(self.tag2)
                    self.comparison_graph.nodes[node]["color"] = self.merge_color
                # There is an identity match but tha names (unstandardized)
                # don't match. So we merge these nodes later
                elif node_data['concept_identity_key'] in model1_identity_keys:
                    # Make sure the color will be the merge color
                    matching_node = model1_identity_keys[node_data['concept_identity_key']]
                    self.comparison_graph.nodes[matching_node]["color"] = self.merge_color
                    # We still add the node, it will be contracted later
                    node_data["tags"] = {self.tag2}
                    node_data["color"] = self.merge_color
                    self.comparison_graph.add_node(node, **node_data)
                    # Add to the list of contracted nodes
                    to_contract.add((node, matching_node))
                # There is no match so we add a new node
                else:
                    # If node doesn't exist, add it
                    node_data["tags"] = {self.tag2}
                    node_data["color"] = self.tag2_color
                    self.comparison_graph.add_node(node, **node_data)

        def extend_data(d, color):
            d["color"] = color
            return d

        self.comparison_graph.add_edges_from(
            ((*u, self.tag1) if u in template_node_ids else u,
             (*v, self.tag1) if v in template_node_ids else v,
             extend_data(d, self.tag1_color))
            for u, v, d in self.templ1_graph.edges(data=True)
        )
        self.comparison_graph.add_edges_from(
            ((*u, self.tag2) if u in template_node_ids else u,
             (*v, self.tag2) if v in template_node_ids else v,
             extend_data(d, self.tag2_color))
            for u, v, d in self.templ2_graph.edges(data=True)
        )

        # Add lookup of concepts so we can add refinement edges
        templ1_concepts = {}
        for templ1 in self.template_model1.templates:
            for concept in templ1.get_concepts():
                key = get_concept_graph_key(concept)
                templ1_concepts[key] = concept
        templ2_concepts = {}
        for templ2 in self.template_model2.templates:
            for concept in templ2.get_concepts():
                key = get_concept_graph_key(concept)
                templ2_concepts[key] = concept

        concept_refinement_edges = []
        joint_concept_keys = set().union(templ1_concepts.keys()).union(templ2_concepts.keys())
        ref_dict = dict(label="refinement_of", color="red", weight=2)
        for (n_a, data_a), (n_b, data_b) in combinations(self.comparison_graph.nodes(data=True), 2):
            if n_a in joint_concept_keys and n_b in joint_concept_keys:
                if self.tag1 in data_a["tags"]:
                    c1 = templ1_concepts[n_a]
                elif self.tag1 in data_b["tags"]:
                    c1 = templ1_concepts[n_b]
                else:
                    continue

                if self.tag2 in data_a["tags"]:
                    c2 = templ2_concepts[n_a]
                elif self.tag2 in data_b["tags"]:
                    c2 = templ2_concepts[n_b]
                else:
                    continue
                if c1.is_equal_to(c2, with_context=True):
                    continue
                if c1.refinement_of(c2,
                                    refinement_func=self.refinement_func,
                                    with_context=True):
                    concept_refinement_edges.append((n_a, n_b, ref_dict))
                if c2.refinement_of(c1,
                                    refinement_func=self.refinement_func,
                                    with_context=True):
                    concept_refinement_edges.append((n_b, n_a, ref_dict))

        if concept_refinement_edges:
            self.comparison_graph.add_edges_from(concept_refinement_edges)

        for u, v in to_contract:
            self.comparison_graph = \
                nx.contracted_nodes(self.comparison_graph, u, v)

    def _assemble_comparison(self):
        self._add_graphs()

        for templ1, templ2 in product(self.template_model1.templates,
                                      self.template_model2.templates):
            # Check for refinement and equality
            if templ1.is_equal_to(templ2, with_context=True):
                self._add_edge(
                    source=templ1,
                    source_tag=self.tag1,
                    target=templ2,
                    target_tag=self.tag2,
                    edge_type="is_equal",
                )
            elif templ1.refinement_of(templ2,
                                      refinement_func=self.refinement_func,
                                      with_context=True):
                self._add_edge(
                    source=templ1,
                    source_tag=self.tag1,
                    target=templ2,
                    target_tag=self.tag2,
                    edge_type="refinement_of",
                )
            elif templ2.refinement_of(templ1,
                                      refinement_func=self.refinement_func,
                                      with_context=True):
                self._add_edge(
                    source=templ2,
                    source_tag=self.tag2,
                    target=templ1,
                    target_tag=self.tag1,
                    edge_type="refinement_of",
                )

    def draw_graph(
        self, path: str, prog: str = "dot", args: str = "", format: Optional[str] = None
    ):
        """Draw a pygraphviz graph of the differences using

        Parameters
        ----------
        path :
            The path to the output file
        prog :
            The graphviz layout program to use, such as "dot", "neato", etc.
        format :
            Set the file format explicitly
        args :
            Additional arguments to pass to the graphviz bash program as a
            string. Example: "args="-Nshape=box -Edir=forward -Ecolor=red "
        """
        # draw graph
        agraph = nx.nx_agraph.to_agraph(self.comparison_graph)
        agraph.draw(path, format=format, prog=prog, args=args)

    def graph_as_json(self) -> Dict:
        """Return the comparison graph json serializable node-link data"""
        return nx.node_link_data(self.comparison_graph)


def get_concept_graph_key(concept: Concept) -> Tuple[str, ...]:
    grounding_key = ("identity", concept.get_curie_str())
    context_key = tuple(i for t in sorted(concept.context.items()) for i in t)
    key = (concept.name,) + grounding_key + context_key
    key = tuple(key) if len(key) > 1 else (key[0],)
    return key


def get_template_graph_key(template: Template) -> Tuple[str, ...]:
    name: str = template.type
    key = [name]
    for concept in template.get_concepts():
        for key_part in get_concept_graph_key(concept):
            key.append(key_part)

    if len(key) > 1:
        return tuple(key)
    else:
        return key[0],


class RefinementClosure:
    """A wrapper class for storing a transitive closure and exposing a
    function to check for refinement relationship.

    Typical usage would involve:
    >>> from mira.dkg.web_client import get_transitive_closure_web
    >>> rc = RefinementClosure(get_transitive_closure_web())
    >>> rc.is_ontological_child('doid:0080314', 'bfo:0000016')
    """
    def __init__(self, transitive_closure):
        self.transitive_closure = transitive_closure

    def is_ontological_child(self, child_curie: str, parent_curie: str) -> bool:
        return (child_curie, parent_curie) in self.transitive_closure


def get_dkg_refinement_closure():
    """Return a refinement closure from the DKG"""
    # Import here to avoid dependency upon module import
    from mira.dkg.web_client import get_transitive_closure_web
    rc = RefinementClosure(get_transitive_closure_web())
    return rc


def main():
    """Generate the JSON schema file."""
    schema = get_json_schema()
    SCHEMA_PATH.write_text(json.dumps(schema, indent=2))


if __name__ == "__main__":
    main()
