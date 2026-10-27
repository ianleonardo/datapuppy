# **Product Requirements Document (PRD): AI-Powered SQL Assistant & RAG Engine**

## **1\. Executive Summary**

This application is an intelligent, AI-driven SQL workbench and data analysis platform. It bridges the gap between natural language and complex database architectures by leveraging Retrieval-Augmented Generation (RAG). Users can interact with multiple databases (Oracle, MariaDB, Postgres, DuckDB) through an intelligent chat interface that not only constructs and executes SQL queries but also interprets the results and recommends next analytical steps.

## **2\. Target Audience**

* **Data Analysts & Business Users:** Need to extract and interpret data without writing complex SQL.  
* **System Analysts & Engineers:** Require a rapid prototyping workspace to test queries, validate schemas, and analyze data structures across different regional databases.  
* **Administrators:** Need to govern database access, configure LLM endpoints, and manage regional guidelines.

## **3\. Functional Requirements**

### **3.1 RAG & Knowledge Management Module**

* **Document Upload & Embedding:** Users can upload reference documents (e.g., data dictionaries, business rules, standard operating procedures) to be vectorized and stored in the RAG vector database.  
* **Contextual Scoping:**  
  * **Global References:** Applicable across the entire application and all databases.  
  * **Country/DB-Specific References:** Tagged to specific regions or databases (e.g., "Singapore Compliance Guidelines" mapped only to the SG-Oracle instance).  
* **Feature & Guideline Attachment:** Administrators can attach specific business functionalities or guidelines directly to a Country/DB pair to guide the AI on how to construct queries for that specific context.

### **3.2 Connection & Schema Management (The Workbench)**

* **Connection Manager:** Interface to define, securely store, and manage database connections. Supported engines must include Oracle, MariaDB, PostgreSQL, and DuckDB.  
* **Automated Schema Retrieval:** Capability to ping a connected database and retrieve all tables, views, and schemas.  
* **Hierarchical Organization:** Retrieved schemas must be categorized and visually split by Country, Database Name, and Schema.  
* **Intelligent Anomaly Detection (Smelly Naming):**  
  * The system scans table names for temporary, backup, or deprecated naming conventions (e.g., `_BCK`, `_TMP`, `_20251012`).  
  * **Human-in-the-Loop Confirmation:** Flags these tables in the UI and prompts the user to explicitly confirm whether to include or exclude them from the AI's contextual reference library.

### **3.3 Intelligent AI Chat Interface**

* **Natural Language to SQL:** Users can request data in plain text. The AI uses the RAG context and schema references to construct an accurate SQL query.  
* **Query Execution:** The chat interface can seamlessly send the constructed query to the target database and retrieve the payload.  
* **Data Interpretation:** Upon retrieving results, the AI automatically analyzes the data set and provides a plain-text summary or insight.  
* **Action Recommendations:** The AI proactively proposes logical next steps or follow-up queries based on the data returned.  
* **Chat History:** All conversations, generated queries, and insights are saved sequentially and can be reloaded in future sessions.  
* **Excel Export:** A one-click utility to export any returned query payload directly into an `.xlsx` format.

### **3.4 Manual Query Engine**

* **SQL Editor:** A robust text area for users to write or modify SQL queries manually.  
* **Pre-Flight Syntax Check:** Before execution, an AI-driven validation step checks the manual query for syntax errors, structural flaws, or schema mismatches, providing warnings or auto-corrections.

### **3.5 Administration & Governance Module**

* **User & Role Management:** Standard CRUD operations for application users and Role-Based Access Control (RBAC) to restrict access to specific Country/DBs.  
* **LLM Configuration:** Toggle and API configuration settings to switch between primary LLMs, specifically supporting Google Gemini and Spirare.  
* **Application Configurations:** Interfaces to manage core operational lists, including active Country/Region lists and authorized Database lists.

## **4\. Proposed Technical Architecture & Stack**

To ensure a modern, highly responsive, and maintainable workspace, the following technology stack is recommended for rapid development and enterprise scalability:

* **Frontend UI/UX:** React bundled with Tailwind CSS for rapid styling. The interface should default to a Dark Mode theme to reduce eye strain during prolonged analysis sessions.  
* **Rich Text & Chat:** Tiptap and BlockNote for rendering the chat interface, allowing for structured, block-based conversational histories and rich markdown support.  
* **Diagramming:** Native integration with Mermaid.js to automatically visualize database schemas or query execution plans directly in the chat window.  
* **Backend Orchestration:** Python (FastAPI) to handle RAG orchestration, API routing, and AI prompt engineering.  
* **Local Processing Engine:** DuckDB utilized as an in-memory or localized processing engine to handle rapid data manipulation, formatting, and the generation of Excel exports before pushing them to the client.

## **5\. Non-Functional Requirements**

* **Security:** Database credentials must be encrypted at rest (e.g., AES-256). The application must not use the LLM to process highly sensitive PII data payloads—only the database schemas and metadata should be sent to the LLM for query generation.  
* **Performance:** Schema retrieval and anomaly detection should run asynchronously to prevent UI blocking on large enterprise databases.

