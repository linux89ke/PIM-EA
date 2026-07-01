import pandas as pd

def inspect_reasons():
    try:
        df = pd.read_excel("reason.xlsx", engine="openpyxl", dtype=str)
        with open("reasons_output.txt", "w", encoding="utf-8") as f:
            f.write(f"reason.xlsx columns: {df.columns.tolist()}\n")
            f.write(f"Number of rows: {len(df)}\n\n")
            for idx, row in df.iterrows():
                f.write(f"Row {idx}:\n")
                for col in df.columns:
                    f.write(f"  {col}: {row[col]}\n")
                f.write("\n")
    except Exception as e:
        with open("reasons_output.txt", "w", encoding="utf-8") as f:
            f.write(f"Error: {e}\n")

if __name__ == "__main__":
    inspect_reasons()
