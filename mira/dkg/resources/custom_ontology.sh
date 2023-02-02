# This script builds custom subsets of large ontologies for import into the MIRA DKG

# See documentation for installing robot at http://robot.obolibrary.org/
# and for ``robot extract`` on http://robot.obolibrary.org/extract.html
# note that STAR just picks terms and MIREOT allows for subtree selection

robot extract --method STAR --copy-ontology-annotations=true \
    --input-iri https://github.com/EBISPOT/covoc/releases/download/current/covoc.owl \
    --term-file covoc_terms.txt \
    --output-iri https://raw.githubusercontent.com/indralab/mira/main/mira/dkg/resources/covoc_slim.json \
    --output covoc_slim.json

robot extract --method STAR --copy-ontology-annotations=true \
    --input-iri http://www.ebi.ac.uk/efo/efo.owl \
    --term-file efo_terms.txt \
    --output-iri https://raw.githubusercontent.com/indralab/mira/main/mira/dkg/resources/efo_slim.json \
    --output efo_slim.json

robot extract --method MIREOT --copy-ontology-annotations=true \
    --input-iri http://purl.obolibrary.org/obo/ncit.owl \
    --output ncit_slim.json \
    --output-iri https://raw.githubusercontent.com/indralab/mira/main/mira/dkg/resources/ncit_slim.json \
    --branch-from-term "obo:NCIT_C17005" \
    --branch-from-term "obo:NCIT_C25636" \
    --branch-from-term "obo:NCIT_C28320" \
    --branch-from-term "obo:NCIT_C171133" \
    --branch-from-term "obo:NCIT_C28554" \
    --branch-from-term "obo:NCIT_C25179" \
    --branch-from-term "obo:NCIT_C71902" \
    --branch-from-term "obo:NCIT_C154475" \
    --branch-from-term "obo:NCIT_C173636" \
    --branch-from-term "obo:NCIT_C168447" \
    --branch-from-term "obo:NCIT_C15220" \
    --branch-from-term "obo:NCIT_C101887" \
    --branch-from-term "obo:NCIT_C168447" \
    --branch-from-term "obo:NCIT_C47891" \
    --branch-from-term "obo:NCIT_C43234" \
    --branch-from-term "obo:NCIT_C3833" \
    --branch-from-term "obo:NCIT_C25587" \
    --branch-from-term "obo:NCIT_C25549" \
    --branch-from-term "obo:NCIT_C113725" \
    --branch-from-term "obo:NCIT_C25269" \
    --branch-from-term "obo:NCIT_C16210" \
    --branch-from-term "obo:NCIT_C21541"

# Run any arbitrary clean-up
# python cleanup.py

# these ontologies can all be merged together with the following command,
# but this makes provenance a little funky in the DKG build
# robot merge --inputs "*_slim.owl" --output merged.owl
