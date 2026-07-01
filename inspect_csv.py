import pandas as pd
from data_utils import _detect_and_read_csv

def inspect():
    csv_path = r"C:\Users\New\Downloads\productSetsPendingQc.2026-06-02T09_32_00Z.csv"
    with open(csv_path, 'rb') as f:
        df = _detect_and_read_csv(f)
    
    with open("inspect_result.txt", "w", encoding="utf-8") as out:
        out.write(f"CSV shape: {df.shape}\n")
        out.write(f"Columns list: {list(df.columns)}\n")
        
        inspect_cols = [
            "MATERIAL_FAMILY", 
            "DESCRIPTION", 
            "SHORT_DESCRIPTION", 
            "FDA", 
            "CATEGORY_SID", 
            "SELLER_SID", 
            "COUNT_OF_EXISTING_VARIATIONS"
        ]
        
        for col in inspect_cols:
            if col in df.columns:
                non_null = df[df[col].notna() & (df[col].astype(str).str.strip() != '') & (df[col].astype(str).str.lower() != 'nan')]
                out.write(f"\n--- Column: {col} ---\n")
                out.write(f"Non-empty rows count: {len(non_null)} / {len(df)}\n")
                if len(non_null) > 0:
                    out.write("Samples:\n")
                    for val in non_null[col].head(3).tolist():
                        out.write(f"  - {repr(val)[:200]}\n")
            else:
                out.write(f"\n--- Column: {col} is MISSING ---\n")

if __name__ == "__main__":
    inspect()
