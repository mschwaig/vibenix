#!/usr/bin/env jq -f

# Check if an object/array contains function_call anywhere in its structure
def has_function_call: 
  if type == "object" then 
    has("function_call") or (to_entries | map(.value | has_function_call) | any)
  elif type == "array" then
    map(has_function_call) | any
  else
    false
  end
;

# Recursively filter to only include objects containing function_call
# and strip function_call objects to only name and args
def filter_function_calls: 
  if type == "object" then
    if has("function_call") then
      # This object contains a function_call - process it
      to_entries | map(
        if .key == "@ccl_index" then
          # Preserve @ccl_index
          {key: .key, value: .value}
        elif .key == "function_call" then
          # Strip function_call to only name and args
          {key: .key, value: {name: .value.name, args: .value.args}}
        else
          # Recurse on other fields that might contain function calls
          select(.value | has_function_call) |
          {key: .key, value: (.value | filter_function_calls)}
        end
      ) | from_entries
    else
      # No function_call here - recurse to find them
      to_entries | map(
        select(.value | has_function_call) |
        {key: .key, value: (.value | filter_function_calls)}
      ) | from_entries
    end
  elif type == "array" then
    # Keep array items that contain function calls, preserving their structure
    map(
      if has_function_call then
        if has("@ccl_index") then
          # Preserve the @ccl_index and filter the rest
          . as $item |
          ($item | del(.["@ccl_index"]) | filter_function_calls) + {"@ccl_index": $item["@ccl_index"]}
        else
          filter_function_calls
        end
      else
        empty
      end
    )
  else
    .
  end
;

# Apply the filter
filter_function_calls