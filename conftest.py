import sys
from pathlib import Path

# Make the project root importable so the tests can import the scripts
# directly, without needing the package to be installed.
sys.path.insert(0, str(Path(__file__).parent))
