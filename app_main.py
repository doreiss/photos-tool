"""py2app app entry point (build artifact, NOT an importable package module).

All logic lives in the package; this only exists so ``python setup.py py2app`` has a
top-level script to bundle. It launches the same menu-bar app as the
``photos-tool-menubar`` console script.
"""

from photos_tool.menubar import main

if __name__ == "__main__":
    main()
