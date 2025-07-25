import nltk
import spacy
import torch
import sqlite3
import traceback
import pandas as pd
import streamlit as st
from typing import Dict, List
from util.retrieve import retrieve
from util.generation import generate_sql
from util.init import init_models, init_graph
from util.self_improve import find_most_common_query_result
from util.load_data import load_schema, load_data_from_file, compute_embedding

@st.cache_resource
def setup_nltk():
    nltk.download('punkt_tab')
    return True

def execute_sql_query(db_path: str, sql_query: str):
    """Execute SQL query and return results as a pandas DataFrame"""
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query(sql_query, conn)
        conn.close()
        return df, None
    except Exception as e:
        return None, str(e)

def rag_query(
    tokenizer,
    llm_model,
    documents: List[Dict],
    db_id: str,
    query: Dict,
    k: int | List[int] = [1,3,5],
    ) -> Dict:
    """
    One-liner for the full RAG pipeline:
      1. retrieve k neighbours,
      2. sample SQLs,
      3. pick the consensus execution.
    Returns a dict with the SQL and the examples retrieved.
    """
    retrieved = None
    if isinstance(k, list):
        retrieved = []
        for shot in k:
            retrieved.append(retrieve(
                documents=documents,
                q_vec=query["embedding"], 
                k=shot
            ))
    else:
        retrieved = retrieve(
            documents=documents,
            q_vec=query["embedding"], 
            k=k
        )

    candidates = generate_sql(
        tokenizer=tokenizer, 
        llm_model=llm_model,
        device="cuda",
        query=query,
        G=st.session_state.G,
        tables=st.session_state.tables,
        table_embeds=st.session_state.table_embeds,
        column_embeds=st.session_state.column_embeds,
        retrieved_docs=retrieved
    )

    db_path = f"/content/Spider_RAG/spider_data/database/{db_id}/{db_id}.sqlite"
    best_sql = find_most_common_query_result(candidates, db_path)
    return {
        "question": query["question"],
        "generated_sql": best_sql,
        "db_path": db_path
    }

# Only one call to st.set_page_config is allowed
st.set_page_config(
    page_title="Spider RAG SQL Generator",
    page_icon="🕷️",
    layout="wide"
)

# Global variables
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
LLM_MODEL_NAME = "Qwen/Qwen2.5-Coder-14B-Instruct"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Initialize session state
if 'initialized' not in st.session_state:
    st.session_state.initialized = False
    st.session_state.query_history = []
    st.session_state.current_result = None
    st.session_state.current_db_path = None

# Initialize models and data only once
if not st.session_state.initialized:
    with st.spinner("Loading models and data... This may take a few minutes on first run."):
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
            schema, tables, columns_orig, foreign_pairs, pk_set = load_schema(
                schema_file="/content/Spider_RAG/spider_data/tables.json",
                db_id="department_store"
            )
            
            query_embeds, documents, table_embeds, column_embeds = load_data_from_file(
                max_samples=6912, 
                test_samples=88,
                folder_url='https://drive.google.com/drive/folders/1KHfedpn61dmY9TEXskacFiRXueB095xc',
                folder_name='BAAI-bge-m3'
            )
            
            tokenizer, llm_model, embed_model = init_models(
                llm_model_name=LLM_MODEL_NAME,
                embedding_model_name=EMBEDDING_MODEL_NAME,
                device=DEVICE
            )
            
            G = init_graph(
                tables=tables,
                columns_orig=columns_orig,
                foreign_pairs=foreign_pairs,
                pk_set=pk_set,
            )

            setup_nltk()
            
            st.session_state.schema = schema
            st.session_state.tables = tables
            st.session_state.columns_orig = columns_orig
            st.session_state.foreign_pairs = foreign_pairs
            st.session_state.pk_set = pk_set
            st.session_state.G = G
            st.session_state.tokenizer = tokenizer
            st.session_state.llm_model = llm_model
            st.session_state.embed_model = embed_model
            st.session_state.documents = documents
            st.session_state.query_embeds = query_embeds
            st.session_state.table_embeds = table_embeds
            st.session_state.column_embeds = column_embeds
            
            st.session_state.initialized = True
            st.success("Models loaded successfully!")
            
        except Exception as e:
            st.error(f"Error initializing models: {str(e)}")
            st.stop()

# Custom CSS
st.markdown("""
    <style>
        [data-testid="stSidebar"] {
            background-color: #000000 !important;
        }
        [data-testid="stSidebar"] * {
            color: #f5f6fa !important;
        }
        .main .block-container {
            background-color: #f5f6fa !important;
        }
    </style>
""", unsafe_allow_html=True)

# Display device info
with st.sidebar:
    st.image("hyundai.png", width=250)
    st.markdown("## Spider RAG Generator")
    st.markdown("This interface generates SQL queries for the department store database.")
    st.markdown("### 🔍 Model Configuration")
    st.markdown(f"**Embedding Model:** {EMBEDDING_MODEL_NAME}")
    st.markdown(f"**LLM Model:** {LLM_MODEL_NAME}")
    st.markdown(f"**Device:** {DEVICE}")

# Example questions
with st.container():
    st.markdown(" ", unsafe_allow_html=True)  # small spacer
    st.markdown("<div style='padding-top:100px; text-align: center;'>", unsafe_allow_html=True)
    st.markdown("### 💡 Example Questions")
    st.markdown(" 1. What is all the information about the Marketing department?")
    st.markdown(" 2. What are the ids and names of department stores with both marketing and managing departments?")
    st.markdown(" 3. Return the ids of the two department store chains with the most department stores.")
    st.markdown(" 4. What is the id of the department with the least number of staff?")
    st.markdown(" 5. Tell me the employee id of the head of the department with the least employees.")

# Execute query button
if st.session_state.current_result and st.session_state.current_db_path:
    if st.button("▶️ Execute Query", key="execute_query"):
        with st.spinner("Executing SQL query..."):
            df, error = execute_sql_query(st.session_state.current_db_path, st.session_state.current_result)
            
            if error:
                st.error(f"Error executing query: {error}")
            else:
                st.success("Query executed successfully!")
                st.subheader("Query Results:")
                
                # Display results info
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Number of Rows", len(df))
                with col2:
                    st.metric("Number of Columns", len(df.columns))
                
                # Display the dataframe
                st.dataframe(df, use_container_width=True)
                
                # Option to download results as CSV
                csv = df.to_csv(index=False)
                st.download_button(
                    label="📥 Download results as CSV",
                    data=csv,
                    file_name="query_results.csv",
                    mime="text/csv"
                )

# Query history
if 'query_history' in st.session_state and st.session_state.query_history:
    st.markdown("## Query History")
    for i, item in enumerate(reversed(st.session_state.query_history)):
        with st.expander(f"Query {len(st.session_state.query_history) - i}: {item['question'][:50]}..."):
            st.markdown(f"**🧠 Question:** {item['question']}")
            st.code(item['sql'], language='sql')
            
            # Add execute button for history items
            if st.button(f"Execute this query", key=f"exec_hist_{i}"):
                with st.spinner("Executing SQL query..."):
                    df, error = execute_sql_query(item['db_path'], item['sql'])
                    
                    if error:
                        st.error(f"Error executing query: {error}")
                    else:
                        st.success("Query executed successfully!")
                        st.subheader("Query Results:")
                        st.dataframe(df, use_container_width=True)

# Query input
with st.container():
    st.markdown("<div style='padding-top: 80px;'>", unsafe_allow_html=True)
    
    user_question = st.text_input(
        "💬 Enter your question about the department store database:",
        placeholder="e.g., What is the total sales amount for each department?",
        key="user_question_input"
    )

    st.markdown("</div>", unsafe_allow_html=True)

# Generate button
if st.button("Generate SQL", type="primary", disabled=not user_question):
    with st.spinner("Generating SQL query..."):
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
            with torch.no_grad():
                q_vec = compute_embedding(st.session_state.embed_model, user_question)
            
            query_item = {
                "question": user_question,
                "embedding": q_vec
            }
            shots = [0,3,5]
            result = rag_query(
                tokenizer=st.session_state.tokenizer,
                llm_model=st.session_state.llm_model,
                documents=st.session_state.documents,
                db_id="department_store",
                query=query_item,
                k=shots
            )
            
            sql_pred = result["generated_sql"]
            db_path = result["db_path"]
            
            st.success("SQL query generated successfully!")
            st.subheader("Generated SQL:")
            st.code(sql_pred, language='sql')
            
            # Store in session state
            st.session_state.current_result = sql_pred
            st.session_state.current_db_path = db_path
            
            # Add to history
            if 'query_history' not in st.session_state:
                st.session_state.query_history = []
            
            st.session_state.query_history.append({
                "question": user_question,
                "sql": sql_pred,
                "db_path": db_path
            })
            
            # Auto-execute option
            if st.checkbox("Auto-execute generated query", value=True):
                with st.spinner("Executing SQL query..."):
                    df, error = execute_sql_query(db_path, sql_pred)
                    
                    if error:
                        st.error(f"Error executing query: {error}")
                    else:
                        st.success("Query executed successfully!")
                        st.subheader("Query Results:")
                        
                        # Display results info
                        col1, col2 = st.columns(2)
                        with col1:
                            st.metric("Number of Rows", len(df))
                        with col2:
                            st.metric("Number of Columns", len(df.columns))
                        
                        # Display the dataframe
                        st.dataframe(df, use_container_width=True)
                        
                        # Option to download results as CSV
                        csv = df.to_csv(index=False)
                        st.download_button(
                            label="📥 Download results as CSV",
                            data=csv,
                            file_name="query_results.csv",
                            mime="text/csv"
                        )
            
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
        except torch.cuda.OutOfMemoryError:
            st.error("GPU out of memory! Try the following:")
            st.write("1. Restart the kernel/session")
            st.write("2. Use a smaller model")
            st.write("3. Reduce batch size in generation")
            st.write("4. Switch to CPU by changing DEVICE to 'cpu'")
            
        except Exception as e:
            st.error(f"Error generating SQL: {str(traceback.format_exc())}")
            st.write("Debug info:")
            st.write(f"- Question: {user_question}")
            st.write(f"- Device: {DEVICE}")
            if torch.cuda.is_available():
                st.write(f"- GPU Memory: {torch.cuda.memory_allocated() / 1024**3:.2f}GB allocated")

# Footer
st.markdown("---")
st.markdown("Built with Streamlit")

# GPU Cache clear button
if DEVICE == "cuda":
    if st.sidebar.button("🧹 Clear GPU Cache"):
        torch.cuda.empty_cache()
        st.sidebar.success("GPU cache cleared!")
        allocated = torch.cuda.memory_allocated() / 1024**3
        st.sidebar.info(f"Current GPU memory: {allocated:.2f}GB")
