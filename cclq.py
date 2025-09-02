#!/usr/bin/env python3
"""
cclq - jq for CCL (Custom Configuration Language) files

A wrapper around jq that allows querying CCL files by converting to JSON,
running jq, and converting the output back to CCL.

CCL format rules:
- Key-value pairs with "key = value" (keys: alphanumeric, underscore, hyphen only)
- Nested structures with indentation (always 2 spaces)
- Arrays use "= N =" markers where N is 0-based index
- Multi-line strings preserve formatting
- References using @path/to/value syntax
"""

import json
import re
import sys
import subprocess
import tempfile
import os
from typing import Any, Dict, List, Union, Optional, Tuple, TextIO


class CCLParser:
    def __init__(self, lines: List[str]):
        self.lines = lines
        self.current_line = 0
        self.root_data = {}
        self.reference_paths = {}  # Store paths to values for reference resolution
        self.path_to_value = {}  # Store paths to non-reference values for proper resolution
        
    def parse(self, resolve_references: bool = False) -> Dict[str, Any]:
        """Parse the entire CCL file."""
        self.root_data = {}
        self.current_line = 0
        self._parse_block(self.root_data, 0)
        
        if resolve_references:
            return self._resolve_references(self.root_data)
        return self.root_data
    
    def _clean_line(self, line: str) -> str:
        """Remove line numbers if present (format: "   123→content")."""
        return re.sub(r'^\s*\d+→', '', line)
    
    def _get_indent(self, line: str) -> int:
        """Get the indentation level of a line (in spaces)."""
        clean = self._clean_line(line)
        return len(clean) - len(clean.lstrip())
    
    def _is_valid_key(self, key: str) -> bool:
        """Check if a string is a valid key name.
        
        Valid keys contain only:
        - Letters (a-z, A-Z)
        - Numbers (0-9)
        - Underscores (_)
        - Hyphens (-)
        """
        return bool(re.match(r'^[a-zA-Z0-9_-]+$', key))
    
    def _find_equal_sign(self, line: str) -> int:
        """Find the first = sign that represents a key-value separator."""
        pos = line.find('=')
        
        if pos == -1:
            return -1
            
        # Extract potential key
        potential_key = line[:pos].strip()
        
        # Check if this is a valid key
        if not self._is_valid_key(potential_key):
            return -1
            
        return pos
    
    def _parse_block(self, container: Union[Dict, List], base_indent: int, parent_path: str = "") -> None:
        """Parse a block at the given indentation level."""
        last_key = None
        
        while self.current_line < len(self.lines):
            if not self.lines[self.current_line].strip():
                self.current_line += 1
                continue
                
            line = self._clean_line(self.lines[self.current_line])
            indent = self._get_indent(line)
            
            # If we've dedented below our base, return to parent
            if indent < base_indent:
                return
                
            # Skip lines that are more indented than base+2 (they belong to child blocks)
            if indent > base_indent and indent != base_indent + 2:
                self.current_line += 1
                continue
            
            # For continuation of multi-line values
            if indent > base_indent and last_key is not None:
                # This is a continuation of the previous value
                if isinstance(container[last_key], str):
                    # Remove base_indent+2 spaces to preserve relative indentation
                    content = line[base_indent+2:] if len(line) > base_indent+2 else line[indent:]
                    container[last_key] += '\n' + content.rstrip()
                self.current_line += 1
                continue
            
            stripped = line[base_indent:].rstrip() if indent >= base_indent else line.strip()
            
            # Check for array marker: = N =
            array_match = re.match(r'^=\s*(\d+)\s*=$', stripped)
            if array_match:
                index = int(array_match.group(1))  # Already 0-based
                self.current_line += 1
                
                if isinstance(container, list):
                    # Parse the array element
                    element = {}
                    element_path = f"{parent_path}[{index}]"
                    self._parse_block(element, base_indent + 2, element_path)
                    
                    # Add @ccl_index to preserve the original index
                    element['@ccl_index'] = index
                    
                    # Extend list if needed (no null padding)
                    while len(container) <= index:
                        container.append({})
                    container[index] = element
                    last_key = None
                else:
                    raise ValueError(f"Array marker found but container is not a list at line {self.current_line}")
            else:
                # Look for key = value pattern
                eq_pos = self._find_equal_sign(stripped)
                
                if eq_pos >= 0:
                    key = stripped[:eq_pos].strip()
                    value = stripped[eq_pos+1:].strip()
                    self.current_line += 1
                    
                    current_path = f"{parent_path}.{key}" if parent_path else key
                    
                    if value:
                        # Inline value
                        parsed_value = self._parse_value(value)
                        container[key] = parsed_value
                        
                        # Store reference path
                        if isinstance(parsed_value, str) and parsed_value.startswith('@'):
                            self.reference_paths[current_path] = parsed_value
                        else:
                            # Store non-reference value for resolution
                            self.path_to_value[current_path] = parsed_value
                        
                        # Check for multi-line continuation
                        if isinstance(parsed_value, str):
                            start_line = self.current_line
                            while self.current_line < len(self.lines):
                                next_line = self._clean_line(self.lines[self.current_line])
                                next_indent = self._get_indent(next_line)
                                
                                if next_indent > base_indent:
                                    # Part of multi-line string
                                    content = next_line[base_indent+2:] if len(next_line) > base_indent+2 else next_line[next_indent:]
                                    container[key] += '\n' + content.rstrip()
                                    self.current_line += 1
                                else:
                                    break
                            
                            # Update path_to_value with final multi-line value if not a reference
                            if not container[key].startswith('@'):
                                self.path_to_value[current_path] = container[key]
                        
                        last_key = key
                    else:
                        # Empty value - check what comes next
                        if self.current_line < len(self.lines):
                            next_line = self._clean_line(self.lines[self.current_line])
                            next_indent = self._get_indent(next_line)
                            next_stripped = next_line.strip()
                            
                            # Check if next line is an array marker
                            if re.match(r'^=\s*\d+\s*=$', next_stripped):
                                # This is an array
                                container[key] = []
                                self._parse_block(container[key], base_indent + 2, current_path)
                                last_key = None
                            elif next_indent > base_indent:
                                # Could be nested structure or multi-line string
                                # Peek to see if it has = signs (likely nested structure)
                                temp_line = self.current_line
                                is_nested = False
                                
                                while temp_line < len(self.lines):
                                    peek_line = self._clean_line(self.lines[temp_line])
                                    peek_indent = self._get_indent(peek_line)
                                    if peek_indent < next_indent:
                                        break
                                    if peek_indent == next_indent:
                                        peek_stripped = peek_line[peek_indent:].strip()
                                        # Check if this line would be parsed as a key-value pair
                                        eq_pos = self._find_equal_sign(peek_stripped)
                                        if eq_pos >= 0:
                                            is_nested = True
                                            break
                                    temp_line += 1
                                
                                if is_nested:
                                    # Nested object
                                    container[key] = {}
                                    self._parse_block(container[key], base_indent + 2, current_path)
                                else:
                                    # Multi-line string
                                    lines = []
                                    while self.current_line < len(self.lines):
                                        ml_line = self._clean_line(self.lines[self.current_line])
                                        ml_indent = self._get_indent(ml_line)
                                        if ml_indent < next_indent:
                                            break
                                        # Preserve relative indentation
                                        content = ml_line[next_indent:] if len(ml_line) > next_indent else ""
                                        lines.append(content.rstrip())
                                        self.current_line += 1
                                    container[key] = '\n'.join(lines)
                                    
                                    # Store non-reference multi-line value
                                    if not container[key].startswith('@'):
                                        self.path_to_value[current_path] = container[key]
                                        
                                last_key = None
                            else:
                                # Empty value
                                container[key] = ""
                                self.path_to_value[current_path] = ""
                                last_key = key
                else:
                    # No = found, this might be content or we should skip
                    self.current_line += 1
    
    def _parse_value(self, value: str) -> Any:
        """Parse a value string into appropriate type."""
        value = value.strip()
        
        # Remove quotes if present
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            return value[1:-1]
        
        # Check for boolean
        if value.lower() == 'true':
            return True
        elif value.lower() == 'false':
            return False
        
        # Check for integer only (no float support)
        try:
            return int(value)
        except ValueError:
            pass
        
        # Return as string
        return value
    
    def _resolve_references(self, data: Any, current_path: str = "") -> Any:
        """Resolve @references in the data structure."""
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                new_path = f"{current_path}.{key}" if current_path else key
                if isinstance(value, str) and value.startswith('@'):
                    # This is a reference - resolve it
                    ref_value = value.strip()  # Remove any trailing whitespace/newlines
                    if not ref_value.startswith('@'):
                        # After stripping, it's not a reference anymore
                        result[key] = value
                        continue
                        
                    ref_path = ref_value[1:]  # Remove @
                    
                    # Resolve transitively - keep following references
                    resolved = None
                    visited = set()  # Prevent infinite loops
                    current_ref = ref_path
                    
                    while current_ref and current_ref not in visited:
                        visited.add(current_ref)
                        
                        # First check path_to_value for non-reference values
                        if current_ref in self.path_to_value:
                            resolved = self.path_to_value[current_ref]
                            break
                        else:
                            # Get value at path
                            val = self._get_value_at_path(self.root_data, current_ref)
                            if val is None:
                                break
                            elif isinstance(val, str) and val.strip().startswith('@'):
                                # Follow the reference
                                current_ref = val.strip()[1:]
                            else:
                                resolved = val
                                break
                    
                    result[key] = resolved if resolved is not None else value
                else:
                    result[key] = self._resolve_references(value, new_path)
            return result
        elif isinstance(data, list):
            return [self._resolve_references(item, f"{current_path}[{i}]") 
                    for i, item in enumerate(data)]
        else:
            return data
    
    def _get_value_at_path(self, data: Any, path: str) -> Any:
        """Get value at a given path like 'key1/key2/key3'."""
        parts = path.split('/')
        current = data
        
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        
        return current


class CCLWriter:
    """Convert JSON data to CCL format."""
    
    def __init__(self, strict_arrays: bool = False):
        self.lines = []
        self.strict_arrays = strict_arrays
        
    def write(self, data: Any, indent: int = 0, parent_key: str = None) -> str:
        """Convert data to CCL format string."""
        self.lines = []
        self._write_value(data, indent, parent_key)
        return '\n'.join(self.lines)
    
    def _write_value(self, value: Any, indent: int, parent_key: Optional[str] = None) -> None:
        """Write a value at the given indentation level."""
        if isinstance(value, dict):
            self._write_dict(value, indent)
        elif isinstance(value, list):
            self._write_list(value, indent)
        elif isinstance(value, str) and '\n' in value:
            # Multi-line string
            if parent_key:
                # Value was already written with key, just add the content
                lines = value.split('\n')
                for i, line in enumerate(lines):
                    if i == 0:
                        continue  # First line already written with key
                    self.lines.append(' ' * (indent + 2) + line)
            else:
                # Standalone multi-line string
                lines = value.split('\n')
                for line in lines:
                    self.lines.append(' ' * indent + line)
        else:
            # Single-line value
            if parent_key is None:
                self.lines.append(' ' * indent + self._format_value(value))
    
    def _write_dict(self, obj: Dict[str, Any], indent: int) -> None:
        """Write a dictionary at the given indentation level."""
        for key, value in obj.items():
            # Skip @ccl_index key as it's metadata
            if key == '@ccl_index':
                continue
                
            if isinstance(value, (dict, list)):
                # Nested structure
                self.lines.append(' ' * indent + f"{key} =")
                self._write_value(value, indent + 2)
            elif isinstance(value, str) and '\n' in value:
                # Multi-line string
                lines = value.split('\n')
                self.lines.append(' ' * indent + f"{key} = {lines[0]}")
                for line in lines[1:]:
                    self.lines.append(' ' * (indent + 2) + line)
            else:
                # Single-line value
                self.lines.append(' ' * indent + f"{key} = {self._format_value(value)}")
    
    def _write_list(self, lst: List[Any], indent: int) -> None:
        """Write a list at the given indentation level."""
        # Validate that all items are dicts with @ccl_index
        for i, item in enumerate(lst):
            if not isinstance(item, dict):
                raise ValueError(f"CCL lists can only contain dict elements, found {type(item).__name__} at position {i}")
            if '@ccl_index' not in item:
                raise ValueError(f"List item at position {i} missing required @ccl_index key")
        
        if self.strict_arrays:
            # In strict mode, validate consecutive indices starting from 0
            for i, item in enumerate(lst):
                if item['@ccl_index'] != i:
                    raise ValueError(f"Invalid @ccl_index: expected {i}, found {item['@ccl_index']} at position {i}")
            
            # Write items in order
            for i, item in enumerate(lst):
                self.lines.append(' ' * indent + f"= {i} =")
                # Create a copy without @ccl_index for writing
                item_copy = {k: v for k, v in item.items() if k != '@ccl_index'}
                self._write_value(item_copy, indent + 2)
        else:
            # In non-strict mode, use the @ccl_index values as-is
            for item in lst:
                index = item['@ccl_index']
                self.lines.append(' ' * indent + f"= {index} =")
                # Create a copy without @ccl_index for writing
                item_copy = {k: v for k, v in item.items() if k != '@ccl_index'}
                self._write_value(item_copy, indent + 2)
    
    def _format_value(self, value: Any) -> str:
        """Format a single value for CCL output."""
        if isinstance(value, bool):
            return 'true' if value else 'false'
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            # Check if value needs quotes (contains special characters)
            if value.startswith('@') or value.strip() != value:
                return f'"{value}"'
            return value
        else:
            return str(value)


def convert_ccl_to_json(input_file: str, output_file: str = None, resolve_refs: bool = False) -> None:
    """Convert a CCL file to JSON format.
    
    Args:
        input_file: Path to input CCL file
        output_file: Optional path to output JSON file (prints to stdout if not provided)
        resolve_refs: If True, resolve @references to their actual values
    """
    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    parser = CCLParser(lines)
    result = parser.parse(resolve_references=resolve_refs)
    
    # Write output
    json_str = json.dumps(result, indent=2, ensure_ascii=False)
    
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(json_str)
        print(f"Converted {input_file} to {output_file}")
    else:
        print(json_str)


def convert_json_to_ccl(input_file: str, output_file: str = None, strict_arrays: bool = False) -> None:
    """Convert a JSON file to CCL format.
    
    Args:
        input_file: Path to input JSON file
        output_file: Optional path to output CCL file (prints to stdout if not provided)
        strict_arrays: If True, enforce consecutive array indices starting from 0
    """
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    writer = CCLWriter(strict_arrays=strict_arrays)
    ccl_str = writer.write(data)
    
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(ccl_str)
        print(f"Converted {input_file} to {output_file}")
    else:
        print(ccl_str)


def test_round_trip(input_file: str, direction: str = "ccl-json-ccl") -> bool:
    """Test round-trip conversion to verify isomorphism.
    
    Args:
        input_file: Path to input file
        direction: Either "ccl-json-ccl" or "json-ccl-json"
    
    Returns:
        True if round-trip conversion preserves the data
    """
    import tempfile
    import os
    
    if direction == "ccl-json-ccl":
        # CCL -> JSON -> CCL
        with open(input_file, 'r', encoding='utf-8') as f:
            original_lines = f.readlines()
        
        # Parse CCL to JSON
        parser = CCLParser(original_lines)
        json_data = parser.parse()
        
        # Convert back to CCL
        writer = CCLWriter(strict_arrays=True)
        ccl_output = writer.write(json_data)
        
        # Parse the generated CCL
        parser2 = CCLParser(ccl_output.split('\n'))
        json_data2 = parser2.parse()
        
        # Compare JSON representations
        if json_data == json_data2:
            print("✓ Round-trip successful: CCL -> JSON -> CCL")
            return True
        else:
            print("✗ Round-trip failed: Data not preserved")
            print("Original JSON:", json.dumps(json_data, indent=2)[:500] + "...")
            print("Round-trip JSON:", json.dumps(json_data2, indent=2)[:500] + "...")
            return False
            
    elif direction == "json-ccl-json":
        # JSON -> CCL -> JSON
        with open(input_file, 'r', encoding='utf-8') as f:
            original_data = json.load(f)
        
        # Convert to CCL
        writer = CCLWriter(strict_arrays=True)
        ccl_str = writer.write(original_data)
        
        # Parse back to JSON
        parser = CCLParser(ccl_str.split('\n'))
        json_data = parser.parse()
        
        # Compare
        if original_data == json_data:
            print("✓ Round-trip successful: JSON -> CCL -> JSON")
            return True
        else:
            print("✗ Round-trip failed: Data not preserved")
            print("Original:", json.dumps(original_data, indent=2)[:500] + "...")
            print("Round-trip:", json.dumps(json_data, indent=2)[:500] + "...")
            return False
    else:
        raise ValueError("Invalid direction. Use 'ccl-json-ccl' or 'json-ccl-json'")


def run_cclq(jq_filter: str, input_files: List[str] = None, jq_args: List[str] = None, 
             output_format: str = 'ccl', resolve_refs: bool = False, strict_arrays: bool = False) -> None:
    """Run jq on CCL files.
    
    Args:
        jq_filter: The jq filter expression
        input_files: List of input CCL files (use stdin if None)
        jq_args: Additional arguments to pass to jq
        output_format: Output format ('ccl' or 'json')
        resolve_refs: If True, resolve @references before processing
        strict_arrays: If True, enforce consecutive array indices starting from 0
    """
    jq_args = jq_args or []
    
    # Handle input
    if input_files:
        # Merge multiple CCL files into an array if more than one
        if len(input_files) == 1:
            with open(input_files[0], 'r', encoding='utf-8') as f:
                lines = f.readlines()
            parser = CCLParser(lines)
            json_data = parser.parse(resolve_references=resolve_refs)
        else:
            # Multiple files: create an array
            json_data = []
            for input_file in input_files:
                with open(input_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                parser = CCLParser(lines)
                file_data = parser.parse(resolve_references=resolve_refs)
                json_data.append(file_data)
    else:
        # Read from stdin
        lines = sys.stdin.readlines()
        parser = CCLParser(lines)
        json_data = parser.parse(resolve_references=resolve_refs)
    
    # Create temporary JSON file for jq input
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_in:
        json.dump(json_data, temp_in, ensure_ascii=False)
        temp_in_path = temp_in.name
    
    try:
        # Run jq
        jq_cmd = ['jq'] + jq_args + [jq_filter, temp_in_path]
        result = subprocess.run(jq_cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"jq error: {result.stderr}", file=sys.stderr)
            sys.exit(result.returncode)
        
        # Process output
        if output_format == 'json':
            # Just output the JSON
            print(result.stdout, end='')
        else:
            # Convert back to CCL
            output = result.stdout.strip()
            
            if not output:
                return
                
            # Try to parse as a single JSON document first
            try:
                json_output = json.loads(output)
                writer = CCLWriter(strict_arrays=strict_arrays)
                ccl_output = writer.write(json_output)
                print(ccl_output)
            except json.JSONDecodeError:
                # Handle streaming output (one JSON per line)
                output_lines = output.split('\n')
                for line in output_lines:
                    if line:
                        try:
                            json_output = json.loads(line)
                            writer = CCLWriter(strict_arrays=strict_arrays)
                            ccl_output = writer.write(json_output)
                            print(ccl_output)
                            if len(output_lines) > 1:
                                print()  # Separate multiple outputs
                        except json.JSONDecodeError:
                            # If it's not valid JSON, just print it
                            print(line)
                        
    finally:
        # Clean up temporary file
        os.unlink(temp_in_path)


def print_usage():
    """Print usage information."""
    print("Usage: cclq [options] <jq filter> [file...]")
    print()
    print("A jq wrapper for CCL (Custom Configuration Language) files.")
    print("Converts CCL to JSON, runs jq, and converts back to CCL.")
    print()
    print("Options:")
    print("  -h, --help          Show this help message")
    print("  -j, --json          Output JSON instead of CCL")
    print("  -r, --resolve-refs  Resolve @references before processing")
    print("  --strict-arrays     Enforce consecutive array indices starting from 0")
    print("  -c, --compact       Compact output (passed to jq)")
    print("  -s, --slurp         Read entire input into array (passed to jq)")
    print("  -e, --exit-status   Set exit status based on output (passed to jq)")
    print("  -n, --null-input    Use null as input (passed to jq)")
    print()
    print("Examples:")
    print("  cclq '.' file.ccl                    # Pretty-print CCL file")
    print("  cclq '.key' file.ccl                 # Extract value of 'key'")
    print("  cclq -j '.' file.ccl                 # Convert CCL to JSON")
    print("  cclq '.[] | select(.id > 5)' f.ccl   # Filter array elements")
    print("  cclq -s '.[0]' file1.ccl file2.ccl   # Get first file's data")
    print()
    print("For ccl/json conversion:")
    print("  cclq --convert <input.ccl> [output.json]")
    print("  cclq --convert <input.json> [output.ccl] --to-ccl")


if __name__ == "__main__":
    # Check for help or no arguments
    if len(sys.argv) < 2 or sys.argv[1] in ['-h', '--help']:
        print_usage()
        sys.exit(0)
    
    # Check for conversion mode (legacy support)
    if len(sys.argv) >= 2 and sys.argv[1] == '--convert':
        # Legacy conversion mode
        args = sys.argv[2:]
        if not args:
            print("Error: --convert requires input file", file=sys.stderr)
            sys.exit(1)
            
        input_file = args[0]
        output_file = args[1] if len(args) > 1 else None
        to_ccl = '--to-ccl' in args
        resolve_refs = '--resolve-refs' in args
        strict_arrays = '--strict-arrays' in args
        
        try:
            if to_ccl:
                convert_json_to_ccl(input_file, output_file, strict_arrays)
            else:
                convert_ccl_to_json(input_file, output_file, resolve_refs)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # jq mode
        jq_args = []
        input_files = []
        output_format = 'ccl'
        resolve_refs = False
        strict_arrays = False
        jq_filter = None
        
        i = 1
        while i < len(sys.argv):
            arg = sys.argv[i]
            
            if arg in ['-j', '--json']:
                output_format = 'json'
            elif arg in ['-r', '--resolve-refs']:
                resolve_refs = True
            elif arg == '--strict-arrays':
                strict_arrays = True
            elif arg in ['-c', '--compact', '-s', '--slurp', '-e', '--exit-status', '-n', '--null-input']:
                jq_args.append(arg)
            elif arg.startswith('-'):
                # Unknown option, pass to jq
                jq_args.append(arg)
            elif jq_filter is None:
                jq_filter = arg
            else:
                # This is an input file
                input_files.append(arg)
            
            i += 1
        
        if jq_filter is None:
            print("Error: No jq filter specified", file=sys.stderr)
            print_usage()
            sys.exit(1)
        
        try:
            run_cclq(jq_filter, input_files or None, jq_args, output_format, resolve_refs, strict_arrays)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)