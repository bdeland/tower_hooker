#!/usr/bin/env python3
"""
Tower Hooker Infrastructure Setup Tool

This script provides easy access to infrastructure setup checking and configuration.

Usage:
    python setup_infrastructure.py status    # Check current setup status
    python setup_infrastructure.py setup     # Run interactive setup wizard
    python setup_infrastructure.py           # Run setup wizard (default)
"""

import sys
import os

# Add src directory to path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

def main():
    """Main entry point for the setup tool"""
    from src.utils.setup_wizard import print_setup_status, run_setup_wizard
    
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == "status":
            print("🔍 Checking Tower Hooker infrastructure setup status...")
            print_setup_status()
        elif command == "setup":
            print("🚀 Starting Tower Hooker infrastructure setup wizard...")
            success = run_setup_wizard()
            sys.exit(0 if success else 1)
        elif command in ["help", "-h", "--help"]:
            print(__doc__)
        else:
            print(f"Unknown command: {command}")
            print(__doc__)
            sys.exit(1)
    else:
        # Default action: run setup wizard
        print("🚀 Starting Tower Hooker infrastructure setup wizard...")
        success = run_setup_wizard()
        sys.exit(0 if success else 1)

if __name__ == "__main__":
    main() 