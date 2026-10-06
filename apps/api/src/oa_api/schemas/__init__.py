"""Pydantic models forming the API's public contract.

Every model a client can receive is declared here, so the OpenAPI document and the
handlers cannot drift apart. The package also carries a test
(``test_no_response_schema_exposes_hidden_tests``) that walks all of these models
and fails if any gains a field capable of carrying hidden tests or a reference
solution.
"""

from __future__ import annotations
