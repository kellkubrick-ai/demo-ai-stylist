import sys

from ai_stylist.cli import main

if __name__ == "__main__":
    sys.argv.insert(1, "import")
    main()
