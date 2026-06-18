import os
import pandas as pd
import psycopg2
from dotenv import load_dotenv

# =========================================================================
# 🎯 CONFIGURATION: CHOOSE CONFIDENCE LEVEL TO VALIDATE
# Options: "High", "Medium", "Low" (Case-insensitive)
# =========================================================================
SELECTED_CONFIDENCE = "High" 
# =========================================================================

# Resolve relative pathing for mti-brain/backend/.env
current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.abspath(os.path.join(current_dir, "..", "..", "backend", ".env"))

if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path)
    print(f"✅ Loaded .env environment from: {env_path}")
else:
    print(f"⚠️ Warning: .env file not found at: {env_path}")
    load_dotenv()

def get_redshift_connection():
    """Establishes a connection to the Amazon Redshift database."""
    try:
        conn = psycopg2.connect(
            host=os.getenv("REDSHIFT_HOST"),
            port=os.getenv("REDSHIFT_PORT"),
            database=os.getenv("REDSHIFT_DB"),
            user=os.getenv("REDSHIFT_USER"),
            password=os.getenv("REDSHIFT_PASSWORD"),
            connect_timeout=10
        )
        return conn
    except Exception as e:
        print(f"❌ Error connecting to Redshift: {e}")
        return None

def run_validation():
    ods_filename = "inferred_relationships.ods"
    ods_file_path = os.path.join(current_dir, ods_filename)
    
    if not os.path.exists(ods_file_path):
        print(f"❌ Target input file missing! Expected it at: {ods_file_path}")
        return

    print(f"📂 Reading relationship definitions from: {ods_filename}")
    
    try:
        df_mappings = pd.read_excel(ods_file_path, engine="odf")
    except Exception as e:
        print(f"❌ Error parsing .ods spreadsheet engine format: {e}")
        return
    
    # Standardise and Clean Data Input Columns
    df_mappings.columns = df_mappings.columns.str.strip()
    df_mappings['confidence'] = df_mappings['confidence'].astype(str).str.strip().str.capitalize()
    
    # Fallback default schema logic from environment
    default_schema = os.getenv("REDSHIFT_SCHEMA", "public")
    df_mappings['from_schema'] = df_mappings['from_schema'].fillna(default_schema).astype(str).str.strip()
    df_mappings['to_schema'] = df_mappings['to_schema'].fillna(default_schema).astype(str).str.strip()
    
    # 🎯 DYNAMIC FILTER: Clean target variable input and isolate rows
    target_confidence = SELECTED_CONFIDENCE.strip().capitalize()
    df_filtered = df_mappings[df_mappings['confidence'] == target_confidence]
    total_found = len(df_filtered)
    
    if total_found == 0:
        print(f"⚠️ No rows found with '{target_confidence}' confidence inside the sheet. Stopping run.")
        return
        
    print(f"🎯 Filter applied. Found {total_found} rows marked as '{target_confidence}' confidence to check.")

    # Connect to Redshift cluster
    conn = get_redshift_connection()
    if not conn:
        return
    
    cursor = conn.cursor()
    results = []
    print(f"\n🚀 Starting value verification on Redshift cluster for {target_confidence} Confidence entries...")
    
    for index, row in df_filtered.iterrows():
        from_schema = row['from_schema']
        from_table = row['from_table']
        from_column = row['from_column']
        to_schema = row['to_schema']
        to_table = row['to_table']
        to_column = row['to_column']
        confidence = row['confidence']
        reasoning = row['reasoning']
        
        print(f"🔄 Checking [{target_confidence} Confidence]: {from_schema}.{from_table}.{from_column} ➡️ {to_schema}.{to_table}.{to_column}...")
        
        # REDSHIFT OPTIMIZED QUERY TEMPLATE
        validation_query = f"""
        SELECT 
            COUNT(DISTINCT "{from_column}") AS unique_source_values,
            COUNT(DISTINCT case when "{from_column}" in (select "{to_column}" from "{to_schema}"."{to_table}" where "{to_column}" is not null) then "{from_column}" else null end) AS matched_target_values
        FROM "{from_schema}"."{from_table}"
        WHERE "{from_column}" IS NOT NULL;
        """
        
        try:
            cursor.execute(validation_query)
            query_res = cursor.fetchone()
            
            if query_res:
                unique_src = int(query_res[0]) if query_res[0] is not None else 0
                matched_tgt = int(query_res[1]) if query_res[1] is not None else 0
                
                if unique_src > 0:
                    overlap_pct = round((matched_tgt / unique_src) * 100, 2)
                else:
                    overlap_pct = 0.0
            else:
                unique_src, matched_tgt, overlap_pct = 0, 0, 0.0
            
            if unique_src == 0:
                verdict = "WRONG (Source Column is Empty)"
            elif overlap_pct >= 50.0:
                verdict = "CORRECT"
            elif overlap_pct == 0.0:
                verdict = "WRONG"
            else:
                verdict = f"ATTENTION (Low Value Match: {overlap_pct}%)"
                
        except Exception as query_error:
            conn.rollback() 
            unique_src, matched_tgt, overlap_pct = 0, 0, 0.0
            clean_error = str(query_error).replace("\n", " ").strip()
            verdict = f"WRONG (SQL Error: {clean_error})"
        
        results.append({
            "From Schema": from_schema,
            "From Table": from_table,
            "From Column": from_column,
            "To Schema": to_schema,
            "To Table": to_table,
            "To Column": to_column,
            "Input Confidence": confidence,
            "Original Reasoning": reasoning,
            "Unique Source Values Checked": unique_src,
            "Matched Target Values": matched_tgt,
            "Actual Value Overlap %": overlap_pct,
            "Final System Verdict": verdict
        })
        
    cursor.close()
    conn.close()
    
    # Export Automated Report back inside the same folder directory
    if results:
        df_output = pd.DataFrame(results)
        output_filename = f"lineage_{target_confidence.lower()}_value_validation_report.xlsx"
        output_path = os.path.join(current_dir, output_filename)
        
        df_output.to_excel(output_path, index=False)
        print(f"\n✅ Evaluation complete! {target_confidence} confidence results exported to: {output_path}")

if __name__ == "__main__":
    run_validation()
