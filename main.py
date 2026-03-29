#!/usr/bin/env python3
"""
HR Document Generator - Main Entry Point
Generates Attestation de Travail and Ordre de Mission documents
"""
import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.ui.main_window import main

if __name__ == "__main__":
    main()
