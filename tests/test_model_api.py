import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import List, Union

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mira.examples.sir import sir_parameterized, sir
from mira.dkg.model import model_blueprint, ModelComparisonResponse
from mira.dkg.api import RelationQuery
from mira.dkg.web_client import is_ontological_child_web, get_relations_web
from mira.metamodel import Concept, ControlledConversion, NaturalConversion, \
    TemplateModel, Distribution
from mira.metamodel.ops import stratify
from mira.metamodel.templates import SympyExprStr
from mira.metamodel.comparison import TemplateModelComparison, \
    TemplateModelDelta, RefinementClosure
from mira.modeling import Model
from mira.modeling.askenet.petrinet import AskeNetPetriNetModel
from mira.modeling.bilayer import BilayerModel
from mira.modeling.petri import PetriNetModel, PetriNetResponse
from mira.modeling.viz import GraphicalModel
from mira.sources.askenet.petrinet import template_model_from_askenet_json
from mira.sources.bilayer import template_model_from_bilayer
from mira.sources.biomodels import get_sbml_model
from mira.sources.petri import template_model_from_petri_json
from mira.sources.sbml import template_model_from_sbml_string


def sorted_json_str(json_dict, ignore_key=None) -> str:
    if isinstance(json_dict, str):
        return json_dict
    elif isinstance(json_dict, (int, float, SympyExprStr)):
        return str(json_dict)
    elif isinstance(json_dict, (tuple, list, set)):
        return "[%s]" % (
            ",".join(sorted(sorted_json_str(s, ignore_key) for s in json_dict))
        )
    elif isinstance(json_dict, dict):
        if ignore_key is not None:
            dict_gen = (
                str(k) + sorted_json_str(v, ignore_key)
                for k, v in json_dict.items()
                if k != ignore_key
            )
        else:
            dict_gen = (
                str(k) + sorted_json_str(v, ignore_key) for k, v in json_dict.items()
            )
        return "{%s}" % (",".join(sorted(dict_gen)))
    elif json_dict is None:
        return json.dumps(json_dict)
    else:
        raise TypeError("Invalid type: %s" % type(json_dict))


def _get_sir_templatemodel() -> TemplateModel:
    infected = Concept(
        name="infected population", identifiers={"ido": "0000511"}
    )
    susceptible = Concept(
        name="susceptible population", identifiers={"ido": "0000514"}
    )
    immune = Concept(name="immune population", identifiers={"ido": "0000592"})

    template1 = ControlledConversion(
        controller=infected,
        subject=susceptible,
        outcome=infected,
    )
    template2 = NaturalConversion(subject=infected, outcome=immune)
    return TemplateModel(templates=[template1, template2])


class MockNeo4jClient:
    @staticmethod
    def query_relations(
        source_curie: str,
        relation_type: Union[str, List[str]],
        target_curie: str,
    ) -> List:
        rq = RelationQuery(
            source_curie=source_curie,
            target_curie=target_curie,
            relations=relation_type,
        )
        res = get_relations_web(relations_model=rq)
        return [r.dict(exclude_unset=True) for r in res]


class State:
    def __init__(self):
        self.client = MockNeo4jClient()
        self.refinement_closure = RefinementClosure(
            {('doid:0080314', 'bfo:0000016')}
        )


class TestModelApi(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        """Set up the test case"""
        self.test_app = FastAPI()
        self.test_app.state = State()
        self.test_app.include_router(model_blueprint, prefix="/api")
        self.client = TestClient(self.test_app)
        self.temp_files: List[Path] = []

    def tearDown(self) -> None:
        for path in self.temp_files:
            path.unlink(missing_ok=True)

    def _get_tmp_file(self, file_ending: str):
        tdp = Path(tempfile.gettempdir())
        tmpfile = tdp.joinpath(f"{uuid.uuid4()}.{file_ending}")
        self.temp_files.append(tmpfile)
        return tmpfile

    def test_petri(self):
        """Test the petrinet endpoint."""
        sir_model_templ = _get_sir_templatemodel()
        response = self.client.post(
            "/api/to_petrinet_acsets", json=sir_model_templ.dict()
        )
        self.assertEqual(response.status_code, 200, msg=response.content)
        response_petri_net = PetriNetResponse.parse_obj(response.json())
        model = Model(sir_model_templ)
        petri_net = PetriNetModel(model)
        self.assertEqual(petri_net.to_pydantic(), response_petri_net)

    def test_petri_parameterized(self):
        response = self.client.post(
            "/api/to_petrinet_acsets", json=json.loads(sir_parameterized.json())
        )
        self.assertEqual(200, response.status_code, msg=response.content)

    def test_petri_distribution(self):
        sir_distribution = copy.deepcopy(sir_parameterized)
        distr = Distribution(type='StandardUniform',
                             parameters={'minimum': 0.01, 'maximum': 0.5})
        sir_distribution.parameters['beta'].distribution = distr
        response = self.client.post(
            "/api/to_petrinet_acsets", json=json.loads(sir_distribution.json())
        )
        pm = response.json()
        assert pm['T'][0]['tprop']['parameter_distribution'] == distr.json()
        assert json.loads(pm['T'][0]['tprop']['mira_parameter_distributions']) == \
            {'beta': distr.dict()}
        self.assertEqual(200, response.status_code, msg=response.content)

    def test_petri_to_template_model(self):
        petrinet_json = PetriNetModel(Model(sir)).to_json()
        tm = template_model_from_petri_json(petrinet_json)
        response = self.client.post("/api/from_petrinet_acsets", json=petrinet_json)
        self.assertEqual(200, response.status_code, msg=response.content)
        resp_json_str = sorted_json_str(response.json())
        tm_json_str = sorted_json_str(tm.dict())
        self.assertEqual(resp_json_str, tm_json_str)

    def test_petri_to_template_model_parameterized(self):
        petrinet_json = PetriNetModel(Model(sir_parameterized)).to_json()
        tm = template_model_from_petri_json(petrinet_json)
        response = self.client.post("/api/from_petrinet_acsets", json=petrinet_json)
        self.assertEqual(200, response.status_code, msg=response.content)
        resp_json_str = sorted_json_str(response.json())
        tm_json_str = sorted_json_str(tm.dict())
        self.assertEqual(resp_json_str, tm_json_str)

    def test_askenet_to_template_model(self):
        askenet_json = AskeNetPetriNetModel(Model(sir_parameterized)).to_json()
        response = self.client.post("/api/from_petrinet", json=askenet_json)
        self.assertEqual(200, response.status_code, msg=response.content)
        template_model = TemplateModel.from_json(response.json())
        self.assertIsInstance(template_model, TemplateModel)

    def test_askenet_from_template_model(self):
        response = self.client.post("/api/to_petrinet", json=json.loads(sir_parameterized.json()))
        self.assertEqual(200, response.status_code, msg=response.content)
        template_model = template_model_from_askenet_json(response.json())
        self.assertIsInstance(template_model, TemplateModel)

    def test_stratify(self):
        """Test the stratification endpoint"""
        sir_templ_model = _get_sir_templatemodel()
        key = "city"
        strata = ["geonames:5128581", "geonames:4930956"]
        query_json = {
            "template_model": sir_templ_model.dict(),
            "key": key,
            "strata": strata,
        }
        response = self.client.post("/api/stratify", json=query_json)
        self.assertEqual(200, response.status_code)
        resp_json_str = sorted_json_str(response.json())

        strat_templ_model = stratify(
            template_model=sir_templ_model, key=key, strata=set(strata)
        )
        strat_str = sorted_json_str(strat_templ_model.dict())

        self.assertEqual(strat_str, resp_json_str)

        # Test directed True
        query_json = {
            "template_model": sir_templ_model.dict(),
            "key": key,
            "strata": strata,
            "directed": True,
        }
        response = self.client.post("/api/stratify", json=query_json)
        self.assertEqual(200, response.status_code)
        resp_json_str = sorted_json_str(response.json())

        strat_templ_model = stratify(
            template_model=sir_templ_model,
            key=key,
            strata=set(strata),
            directed=query_json["directed"],
        )
        strat_str = sorted_json_str(strat_templ_model.dict())

        self.assertEqual(strat_str, resp_json_str)

        # todo: test for conversion_cls == "controlled_conversions" when
        #  that works for stratify

    def test_to_dot_file(self):
        sir_templ_model = _get_sir_templatemodel()
        response = self.client.post(
            "/api/viz/to_dot_file", json=sir_templ_model.dict()
        )
        self.assertEqual(200, response.status_code)
        self.assertIn(
            "text/vnd.graphviz",
            response.headers["content-type"],
            f"Got content-type {response.headers['content-type']}",
        )
        gm = GraphicalModel(Model(sir_templ_model))
        tmpf = self._get_tmp_file(file_ending="gv")
        gm.write(path=tmpf, format="dot")
        with open(tmpf, "r") as fi:
            file_str = fi.read()
        self.assertEqual(file_str, response.text)

    def test_to_graph_image(self):
        sir_templ_model = _get_sir_templatemodel()
        response = self.client.post(
            "/api/viz/to_image", json=sir_templ_model.dict()
        )
        self.assertEqual(200, response.status_code)
        self.assertIn(
            "image/png",
            response.headers["content-type"],
            f"Got content-type {response.headers['content-type']}",
        )
        gm = GraphicalModel(Model(sir_templ_model))
        tmpf = self._get_tmp_file(file_ending="png")
        gm.write(path=tmpf, format="png")
        with open(tmpf, "rb") as fi:
            file_str = fi.read()
        self.assertEqual(file_str, response.content)

    def test_biomodels_id_to_template_model(self):
        model_id = "BIOMD0000000956"
        response = self.client.get(f"/api/biomodels/{model_id}",
                                   params={'simplify_rate_laws': True})
        self.assertEqual(200, response.status_code)

        # Try to make a template model from the json
        tm = TemplateModel.from_json(response.json())

        # Test against locally made template model
        xml_string = get_sbml_model(model_id=model_id)
        local = template_model_from_sbml_string(
            xml_string, model_id=model_id
        )
        self.assertEqual(
            sorted_json_str(tm.dict()), sorted_json_str(local.dict())
        )

    def test_workflow(self):
        """Test downloading a BioModel and converting to PetriNet."""
        biomodel_response = self.client.get("/api/biomodels/BIOMD0000000956")
        self.assertEqual(200, biomodel_response.status_code)
        petrinet_response = self.client.post("/api/to_petrinet_acsets", json=biomodel_response.json())
        self.assertEqual(200, petrinet_response.status_code)
        petrinet_json = petrinet_response.json()
        self.assertIn("S", petrinet_json)

    def test_biomodels_id_bad_request(self):
        response = self.client.get(f"/api/biomodels/not_a_model")
        self.assertEqual(400, response.status_code)

    def test_bilayer_json_to_template_model(self):
        from mira.examples.sir import sir_bilayer

        response = self.client.post("/api/bilayer_to_model", json=sir_bilayer)
        self.assertEqual(response.status_code, 200)

        # Try to make a TemplateModel of the json
        tm = TemplateModel.from_json(response.json())
        tm2 = template_model_from_bilayer(bilayer_json=sir_bilayer)
        sorted1 = sorted(tm.templates, key=lambda t: t.get_key())
        sorted2 = sorted(tm2.templates, key=lambda t: t.get_key())
        assert all(t1.is_equal_to(t2) for t1, t2 in zip(sorted1, sorted2))

    def test_template_model_to_bilayer_json(self):
        from mira.examples.sir import sir_bilayer

        tm = template_model_from_bilayer(bilayer_json=sir_bilayer)
        bj = BilayerModel(Model(tm)).bilayer

        response = self.client.post("/api/model_to_bilayer",
                                    json=json.loads(tm.json()))
        self.assertEqual(response.status_code, 200)
        bj_res = response.json()

        self.assertEqual(sorted_json_str(bj), sorted_json_str(bj_res))

    def test_xml_str_to_template_model(self):
        model_id = "BIOMD0000000956"
        xml_string = get_sbml_model(model_id=model_id)

        response = self.client.post(
            "/api/sbml_xml_to_model", json={"xml_string": xml_string}
        )
        self.assertEqual(response.status_code, 200)
        tm_res = TemplateModel.from_json(response.json())

        local = template_model_from_sbml_string(xml_string)
        self.assertEqual(
            sorted_json_str(tm_res.dict()), sorted_json_str(local.dict())
        )

    def test_models_to_templatemodel_delta_graph_json(self):
        sir_templ_model = _get_sir_templatemodel()
        sir_templ_model_ctx = TemplateModel(
            templates=[
                t.with_context(location="geonames:5128581")
                for t in sir_templ_model.templates
            ]
        )

        response = self.client.post(
            "/api/models_to_delta_graph",
            json={
                "template_model1": sir_templ_model.dict(),
                "template_model2": sir_templ_model_ctx.dict(),
            },
        )
        self.assertEqual(200, response.status_code)

        tmd = TemplateModelDelta(
            template_model1=sir_templ_model,
            template_model2=sir_templ_model_ctx,
            # If the dkg is out of sync with what is on the server,
            # the is_ontological_child functions might give different results
            refinement_function=is_ontological_child_web,
        )
        local_str = sorted_json_str(tmd.graph_as_json())
        resp_str = sorted_json_str(response.json())

        self.assertEqual(local_str, resp_str)

    def test_models_to_templatemodel_delta_graph_image(self):
        sir_templ_model = _get_sir_templatemodel()
        sir_templ_model_ctx = TemplateModel(
            templates=[
                t.with_context(location="geonames:5128581")
                for t in sir_templ_model.templates
            ]
        )

        response = self.client.post(
            "/api/models_to_delta_image",
            json={
                "template_model1": sir_templ_model.dict(),
                "template_model2": sir_templ_model_ctx.dict(),
            },
        )
        self.assertEqual(200, response.status_code)
        self.assertIn(
            "image/png",
            response.headers["content-type"],
            f"Got content-type {response.headers['content-type']}",
        )
        tmd = TemplateModelDelta(
            template_model1=sir_templ_model,
            template_model2=sir_templ_model_ctx,
            # If the dkg is out of sync with what is on the server,
            # the is_ontological_child functions might give different results
            refinement_function=is_ontological_child_web,
        )

        tmpf = self._get_tmp_file(file_ending="png")
        tmd.draw_graph(path=tmpf.absolute().as_posix())
        with open(tmpf, "rb") as fi:
            file_str = fi.read()
        self.assertEqual(file_str, response.content)

    def test_add_transition(self):
        sir_templ_model = _get_sir_templatemodel()
        s = {'name': 'susceptible population',
             'identifiers': {'ido': '0000514'}}
        x = {'name': 'new_state'}
        response = self.client.post(
            "/api/add_transition",
            json={
                "template_model": sir_templ_model.dict(),
                "subject_concept": s,
                "outcome_concept": x,
                "parameter": {'name': 's_to_x', 'value': 0.1}}
        )
        self.assertEqual(200, response.status_code)

    def test_n_way_comparison(self):
        sir_templ_model = _get_sir_templatemodel()
        sir_templ_model_ctx = TemplateModel(
            templates=[
                t.with_context(location="geonames:5128581")
                for t in sir_templ_model.templates
            ]
        )
        mmts = [sir_templ_model, sir_templ_model_ctx]

        response = self.client.post(
            "/api/model_comparison",
            json={"template_models": [m.dict() for m in mmts]},
        )
        self.assertEqual(200, response.status_code)

        # See if the response json can be parsed with ModelComparisonResponse
        resp_model = ModelComparisonResponse(**response.json())

        # Check that the response is the same as the local version
        # explicitly don't use TemplateModelComparison.from_template_models
        local = TemplateModelComparison(
            template_models=mmts, refinement_func=is_ontological_child_web
        )
        model_comparson_graph_data = local.model_comparison
        local_response = ModelComparisonResponse(
            graph_comparison_data=model_comparson_graph_data,
            similarity_scores=model_comparson_graph_data.get_similarity_scores(),
        )
        self.assertEqual(
            sorted_json_str(local_response.dict()),
            sorted_json_str(resp_model.dict()),
        )
