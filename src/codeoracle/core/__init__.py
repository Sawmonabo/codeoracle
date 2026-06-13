"""Core cross-cutting infrastructure: exceptions, config, logging.

This package holds the platform-wide primitives that every other layer
(models, db, adapters, services, processes) depends on. It must not
import from any other codeoracle.* layer to keep dependency direction inward.
"""
