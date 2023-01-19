from mira.examples.sir import sir, susceptible, infected, recovered
from mira.metamodel import *
from mira.modeling import Model
from mira.modeling.petri import PetriNetModel
from mira.sources.petri import state_to_concept, template_model_from_petri_json


def test_state_to_concept():
    state = {'sname': 'susceptible_population',
             'mira_ids': "[('identity', 'ido:0000514')]",
             'mira_context': "[('city', 'geonames:5128581')]"}
    concept = state_to_concept(state)
    assert concept.name == 'susceptible_population'
    assert concept.identifiers == {'ido': '0000514'}
    assert concept.context == {'city': 'geonames:5128581'}


def test_petri_reverse():
    pm = PetriNetModel(Model(sir))
    pj = pm.to_json()
    tm = template_model_from_petri_json(pj)
    assert len(tm.templates) == 2
    assert isinstance(tm.templates[0], ControlledConversion)
    assert isinstance(tm.templates[1], NaturalConversion)
    assert tm.templates[0].controller.name == infected.name
    assert tm.templates[0].subject.name == susceptible.name
    assert tm.templates[0].outcome.name == infected.name
    assert tm.templates[1].subject.name == infected.name
    assert tm.templates[1].outcome.name == recovered.name
    assert tm.templates[0].controller.identifiers == infected.identifiers
    assert tm.templates[0].subject.identifiers == susceptible.identifiers
    assert tm.templates[0].outcome.identifiers == infected.identifiers
    assert tm.templates[1].subject.identifiers == infected.identifiers
    assert tm.templates[1].outcome.identifiers == recovered.identifiers