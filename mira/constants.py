"""Constants used across MIRA."""

import pystow

__all__ = ["MODULE", "NODE_HEADER", "EDGE_HEADER"]

#: The PyStow module which allows for system-independent
#: creation of stable file paths.
MODULE = pystow.module("mira")

#: The used for the nodes files in the neo4j bulk import
NODE_HEADER = (
    "id:ID",
    ":LABEL",
    "name:string",
    "synonyms:string[]",
    "obsolete:boolean",
    "type:string",
    "description:string",
    "xrefs:string[]",
    "alts:string[]",
    "version:string",
)

#: The used for the edges files in the neo4j bulk import
EDGE_HEADER = (
    ":START_ID",
    ":END_ID",
    ":TYPE",
    "pred:string",
    "source:string",
    "graph:string",
    "version:string",
)
