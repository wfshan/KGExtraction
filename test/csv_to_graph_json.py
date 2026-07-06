import csv
import json
import os

def csv_to_graph_json(csv_path, output_path):
    nodes = set()  # Using a set of tuples (name, type) for uniqueness
    edges = set()  # Using a set of tuples (source, target, type) for uniqueness

    headers = ["审计板块", "审计模块", "审计场景", "审计重点", "审计要点", "审计指标"]
    
    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or len(row) < 6:
                continue
            
            # Clean and add nodes
            row_clean = [cell.strip() for cell in row[:6]]
            for i, name in enumerate(row_clean):
                if name:
                    nodes.add((name, headers[i]))
            
            # Add edges between adjacent columns
            for i in range(5):
                source = row_clean[i]
                target = row_clean[i+1]
                if source and target:
                    # Relation type is "属于" (belongs to) or simple "关系"
                    # Based on the hierarchy, it's Parent -> Child
                    edges.add((source, target, f"{headers[i]}-{headers[i+1]}"))

    # Convert sets to list of dictionaries according to template
    nodes_out = []
    for name, entity_type in nodes:
        nodes_out.append({
            "name": name,
            "entity_type": entity_type,
            "properties": {},
            "confidence": 1.0
        })

    edges_out = []
    for source, target, rel_type in edges:
        edges_out.append({
            "source_name": source,
            "target_name": target,
            "relation_type": rel_type,
            "properties": {},
            "confidence": 1.0
        })

    result = {
        "nodes": nodes_out,
        "edges": edges_out
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    csv_file = "/Users/wfshan/Desktop/软著/KGExtraction/test/sector_index.csv"
    output_file = "/Users/wfshan/Desktop/软著/KGExtraction/test/sector_index.json"
    
    print(f"Processing {csv_file}...")
    csv_to_graph_json(csv_file, output_file)
    print(f"Graph JSON saved to {output_file}")
