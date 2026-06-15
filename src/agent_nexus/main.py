"""Entry point for the agent-nexus command."""
import sys
import os

# Add src to path when running as installed package
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import main

if __name__ == "__main__":
    main()
