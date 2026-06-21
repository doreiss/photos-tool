"""PyInstaller app entry point (build artifact; deliberately NOT under src/, so it never
ships in the wheel). The frozen .app launches the same menu-bar app as the
``photos-tool-menubar`` console script; ``menubar.main()`` also dispatches the
``--pyi-cli`` / ``--pyi-osxphotos`` self-reinvocation sentinels.
"""

from photos_tool.menubar import main

if __name__ == "__main__":
    main()
