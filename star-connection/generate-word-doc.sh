#!/bin/bash

# Script to generate Word document from design documentation

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DESIGN_FILE="DESIGN.md"
OUTPUT_FILE="Star-Connection-Design-Document.docx"

echo "Generating Word document from design documentation..."

# Check if pandoc is installed
if ! command -v pandoc &> /dev/null; then
    echo "Error: pandoc is not installed."
    echo ""
    echo "Please install pandoc to generate Word document:"
    echo ""
    echo "On macOS:"
    echo "  brew install pandoc"
    echo ""
    echo "On Ubuntu/Debian:"
    echo "  sudo apt-get install pandoc"
    echo ""
    echo "On Windows:"
    echo "  Download from: https://pandoc.org/installing.html"
    echo ""
    echo "Alternatively, you can:"
    echo "1. Open DESIGN.md in any markdown editor"
    echo "2. Export to Word/PDF format"
    echo "3. Use online converters like:"
    echo "   - https://cloudconvert.com/md-to-docx"
    echo "   - https://www.markdowntoword.com/"
    echo ""

    # Create a simple HTML version as alternative
    echo "Creating HTML version as alternative..."
    cat > "DESIGN.html" << EOF
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Star Connection Design Document</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }
        h1 { color: #2c3e50; border-bottom: 2px solid #3498db; }
        h2 { color: #34495e; border-bottom: 1px solid #bdc3c7; }
        h3 { color: #7f8c8d; }
        table { border-collapse: collapse; width: 100%; margin: 20px 0; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        code { background-color: #f8f8f8; padding: 2px 4px; border-radius: 3px; }
        pre { background-color: #f8f8f8; padding: 10px; border-radius: 5px; overflow: auto; }
        .note { background-color: #fffde7; padding: 10px; border-left: 4px solid #ffeb3b; margin: 10px 0; }
    </style>
</head>
<body>
EOF

    # Convert markdown to simple HTML (basic conversion)
    sed -e 's/^# \(.*\)$/<h1>\1<\/h1>/' \
        -e 's/^## \(.*\)$/<h2>\1<\/h2>/' \
        -e 's/^### \(.*\)$/<h3>\1<\/h3>/' \
        -e 's/`\([^`]*\)`/<code>\1<\/code>/g' \
        -e 's/^- /• /g' \
        -e 's/|\([^|]*\)|\([^|]*\)|\([^|]*\)|/<tr><td>\1<\/td><td>\2<\/td><td>\3<\/td><\/tr>/g' \
        "$DESIGN_FILE" >> "DESIGN.html"

    cat >> "DESIGN.html" << EOF
</body>
</html>
EOF

    echo "HTML version created: DESIGN.html"
    echo "You can open this file in a browser and save as PDF/Word."
    exit 1
fi

# Generate Word document using pandoc
echo "Converting $DESIGN_FILE to $OUTPUT_FILE using pandoc..."

pandoc "$DESIGN_FILE" -o "$OUTPUT_FILE" \
    --reference-doc=none \
    --table-of-contents \
    --toc-depth=3 \
    --highlight-style=tango \
    --metadata title="Star Connection Design Document" \
    --metadata author="Claude Code Assistant" \
    --metadata date="$(date +'%Y-%m-%d')"

if [ $? -eq 0 ]; then
    echo "Successfully generated: $OUTPUT_FILE"
    echo ""
    echo "Document properties:"
    echo "  - Title: Star Connection Design Document"
    echo "  - Author: Claude Code Assistant"
    echo "  - Date: $(date +'%Y-%m-%d')"
    echo "  - Table of Contents: Yes (3 levels)"
    echo ""
    echo "You can now open $OUTPUT_FILE in Microsoft Word or compatible software."
else
    echo "Failed to generate Word document."
    echo "Please check if pandoc is properly installed and has docx support."
    exit 1
fi