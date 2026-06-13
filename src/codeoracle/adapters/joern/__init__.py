"""Joern adapter — per-file CPG extraction via subprocess.

Workers and IndexingService talk to Joern ONLY through JoernClient.
Direct `subprocess.run(['joern-parse', ...])` calls outside this
package are forbidden by convention (Plan 6 keeps this rule).
"""

from codeoracle.adapters.joern.client import JoernClient
from codeoracle.adapters.joern.config import JoernConfig
from codeoracle.adapters.joern.parser import JoernCpgParser

__all__ = ["JoernClient", "JoernConfig", "JoernCpgParser"]
