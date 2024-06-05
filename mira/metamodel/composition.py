__all__ = [
    "model_compose"
]

from mira.metamodel import *


class AuthorWrapper:
    """
    Wrapper around the Author class that allows for Author object comparison based on the "name"
    attribute of the Author object such that when annotations are merged between two template
    models, Author names won't be duplicated if the two template models being composed share an
    author.
    """

    def __init__(self, author: Author):
        self.author = author

    def __hash__(self):
        return hash(self.author.name)

    def __eq__(self, other):
        if isinstance(other, AuthorWrapper):
            return self.author.name == other.author.name
        return False


def model_compose(tm0, tm1):
    """
    Method composes two template models into one

    Parameters
    ----------
    tm0 :
        The first template model to be composed
    tm1 :
        The second template model to be composed

    Returns
    -------
    :
        The composed template model
    """
    model_list = [tm0, tm1]
    refinement_func = get_dkg_refinement_closure().is_ontological_child
    compare = TemplateModelComparison(model_list, refinement_func=refinement_func)
    comparison_result = compare.model_comparison.get_similarity_scores()

    if comparison_result[0]["score"] == 0:
        # get the union of all template model attributes as the models are 100% distinct
        new_templates = tm0.templates + tm1.templates
        new_parameters = {**tm0.parameters, **tm1.parameters}
        new_initials = {**tm0.initials, **tm1.initials}
        new_observables = {**tm0.observables, **tm1.observables}
        new_annotations = annotation_composition(tm0.annotations, tm1.annotations)

        return TemplateModel(templates=new_templates, parameters=new_parameters,
                             initials=new_initials, observables=new_observables,
                             annotations=new_annotations)

    elif comparison_result[0]['score'] == 1.0:
        # return the first template model as both template models are exactly the same
        return tm0
    else:
        # template models are partially similar
        new_templates = []
        new_parameters = {}
        new_initials = {}
        new_observables = {}
        new_annotations = annotation_composition(tm0.annotations, tm1.annotations)

        # TODO: Verify if pairwise comparison with all templates from both template models is the
        #  correct way to proceed? Would we want to use zip_longest and pad shorter template list?
        #   Using zip_longest means template list order matters.
        for outer_template in tm0.templates:
            for inner_template in tm1.templates:
                if inner_template.refinement_of(outer_template, refinement_func=refinement_func):
                    # inner_template from tm1 is a more specific version of outer_template from tm0

                    # Don't want to add a template that has already been added
                    if inner_template not in new_templates:
                        new_templates.append(inner_template)
                        process_template(inner_template, tm1, new_parameters, new_initials,
                                         new_observables)

                elif outer_template.refinement_of(inner_template, refinement_func=refinement_func):
                    # outer_template from tm0 is a more specific version of inner_template from tm1
                    if outer_template not in new_templates:
                        new_templates.append(outer_template)
                        process_template(outer_template, tm0, new_parameters, new_initials,
                                         new_observables)

                else:
                    # the two templates are disjoint
                    if outer_template not in new_templates:
                        new_templates.append(outer_template)
                        process_template(outer_template, tm0, new_parameters, new_initials,
                                         new_observables)

                    if inner_template not in new_templates:
                        new_templates.append(inner_template)
                        process_template(inner_template, tm1, new_parameters, new_initials,
                                         new_observables)

        return TemplateModel(templates=new_templates, parameters=new_parameters,
                             initials=new_initials, observables=new_observables,
                             annotations=new_annotations)


def process_template(added_template, tm, parameters, initials, observables):
    """
    Helper method that updates the dictionaries that contain the attributes to be used for the
    new composed template model

    Parameters
    ----------
    added_template :
        The template that was added to the list of templates for the composed template model
    tm :
        The input template model to the model_compose method that contains the template to be added
    parameters :
        The dictionary of parameters to update that will be used for the composed template model
    initials :
        The dictionary of initials to update that will be used for the composed template model
    observables :
        The dictionary observables to update that will be used for the composed template model

    """
    parameters.update({param_name: tm.parameters[param_name] for param_name
                       in added_template.get_parameter_names()})
    initials.update({initial_name: tm.initials[initial_name] for
                     initial_name in added_template.get_concept_names()
                     if initial_name in tm.initials})


def update_observables():
    pass


def annotation_composition(tm0_annotations, tm1_annotations):
    """
    Helper method that combines the annotations of the models being composed

    Parameters
    ----------
    tm0_annotations :
        Annotations of the first template model
    tm1_annotations :
        Annotations of the second template model

    Returns
    -------
    :
        The created `Annotations` object from combining the input template model annotations
    """

    if tm0_annotations is None:
        return tm1_annotations
    elif tm1_annotations is None:
        return tm0_annotations
    elif tm0_annotations is None and tm1_annotations is None:
        return None

    new_name = f"{tm0_annotations.name} + {tm1_annotations.name}"
    new_description = (f"First Template Model Description: {tm0_annotations.description}"
                       f"\nSecond Template Model Description: {tm1_annotations.description}")
    new_license = (f"First Template Model License: {tm0_annotations.license}"
                   f"\nSecond Template Model License: {tm1_annotations.license}")

    # Use the AuthorWrapper class here to create a list of Author objects with unique name
    # attributes
    new_authors = tm0_annotations.authors + tm1_annotations.authors
    new_authors = set(AuthorWrapper(author) for author in new_authors)
    new_authors = [wrapper.author for wrapper in new_authors]

    new_references = list(set(tm0_annotations.references) | set(tm1_annotations.references))
    new_locations = list(set(tm0_annotations.locations) | set(tm1_annotations.locations))
    new_pathogens = list(set(tm0_annotations.pathogens) | set(tm1_annotations.pathogens))
    new_diseases = list(set(tm0_annotations.diseases) | set(tm1_annotations.diseases))
    new_hosts = list(set(tm0_annotations.hosts) | set(tm1_annotations.hosts))
    new_model_types = list(set(tm0_annotations.model_types) | set(tm1_annotations.model_types))

    return Annotations(name=new_name, description=new_description,
                       license=new_license, authors=new_authors,
                       references=new_references, locations=new_locations,
                       pathogens=new_pathogens, dieases=new_diseases,
                       hosts=new_hosts, model_types=new_model_types)
