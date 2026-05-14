#!/bin/bash
# Instagram Date Finder - Mac launcher

cd "$(dirname "$0")"

echo ""
echo "  =========================================="
echo "   Instagram Date Finder"
echo "  =========================================="
echo ""

# Install dependencies if needed
echo "  Checking dependencies..."
pip3 install flask pandas openpyxl werkzeug --quiet

echo ""
echo "  Starting server..."
python3 app.py
