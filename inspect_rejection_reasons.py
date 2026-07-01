import pandas as pd

def inspect_rejection_xlsx():
    f = r"C:\Users\New\Downloads\01_ProductSets_RejectionReasons.xlsx"
    try:
        xl = pd.ExcelFile(f)
        print("Sheets in 01_ProductSets_RejectionReasons.xlsx:", xl.sheet_names)
        for sheet in xl.sheet_names[:5]:
            df = pd.read_excel(f, sheet_name=sheet, nrows=5)
            print(f"\nSheet {sheet} columns:", df.columns.tolist())
            print("First 2 rows:")
            print(df.head(2))
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    inspect_rejection_xlsx()
