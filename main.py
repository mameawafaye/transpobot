from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import mysql.connector
import httpx
import re
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connexion MySQL
def get_db_connection():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="bintkhadija",   # ← remplace ici
        database="transpobot"
    )

# Schéma BDD pour le prompt
DB_SCHEMA = """
Tables MySQL disponibles :
- chauffeurs(id, nom, prenom, telephone)
- vehicules(id, immatriculation, statut, kilometrage)
- lignes(id, nom, depart, arrivee)
- trajets(id, chauffeur_id, vehicule_id, ligne_id, date_heure_depart, statut)
- incidents(id, trajet_id, description, date_incident)
- tarifs(id, ligne_id, prix)
"""

SYSTEM_PROMPT = f"""Tu es TranspoBot, assistant IA d'une compagnie de transport.
Tu génères des requêtes SQL MySQL pour répondre aux questions en langage naturel.

{DB_SCHEMA}

RÈGLES ABSOLUES :
1. Génère UNIQUEMENT des requêtes SELECT (jamais INSERT, UPDATE, DELETE, DROP).
2. Réponds UNIQUEMENT avec un JSON valide, sans texte autour, sans balises markdown :
   {{"sql": "SELECT ...", "explication": "courte explication"}}
3. Si la question n'est pas liée aux données, réponds :
   {{"sql": null, "explication": "ta réponse ici"}}
4. Mets toujours LIMIT 50.
"""

# Route test
@app.get("/")
def home():
    return {"message": "API TranspoBot fonctionne !"}

# Liste des vehicules
@app.get("/vehicules")
def get_vehicules():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM vehicules")
    data = cursor.fetchall()
    conn.close()
    return data

# Liste des chauffeurs
@app.get("/chauffeurs")
def get_chauffeurs():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM chauffeurs")
    data = cursor.fetchall()
    conn.close()
    return data

# Liste des trajets
@app.get("/trajets")
def get_trajets():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    query = """
    SELECT t.id, c.nom, c.prenom, v.immatriculation, t.date_heure_depart, t.statut
    FROM trajets t
    JOIN chauffeurs c ON t.chauffeur_id = c.id
    JOIN vehicules v ON t.vehicule_id = v.id
    """
    cursor.execute(query)
    data = cursor.fetchall()
    conn.close()
    return data

# Dashboard
@app.get("/dashboard")
def dashboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM trajets")
    trajets = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM chauffeurs")
    chauffeurs = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM vehicules")
    vehicules = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM incidents")
    incidents = cursor.fetchone()[0]
    conn.close()
    return {
        "total_trajets": trajets,
        "total_chauffeurs": chauffeurs,
        "total_vehicules": vehicules,
        "total_incidents": incidents
    }

# Liste des lignes
@app.get("/lignes")
def get_lignes():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM lignes")
    data = cursor.fetchall()
    conn.close()
    return data

# Liste des tarifs
@app.get("/tarifs")
def get_tarifs():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT t.id, l.nom as ligne_nom, t.prix
        FROM tarifs t
        JOIN lignes l ON t.ligne_id = l.id
        ORDER BY l.nom
    """)
    data = cursor.fetchall()
    conn.close()
    return data

# ── CHATBOT IA ──────────────────────────────────────────────
class Question(BaseModel):
    question: str

@app.post("/chat")
async def chat(q: Question):
    # 1. Envoyer la question à Ollama
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:11434/api/chat",
            json={
                "model": "llama3.2",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": q.question}
                ],
                "stream": False
            },
            timeout=60
        )
    
    content = response.json()["message"]["content"].strip()
    
    # 2. Nettoyer la réponse (enlever ```json si présent)
    content = re.sub(r'```json\s*', '', content)
    content = re.sub(r'```\s*', '', content)
    
    # 3. Parser le JSON
    match = re.search(r'\{.*\}', content, re.DOTALL)
    if not match:
        return {"reponse": content, "data": [], "sql": None}
    
    llm = json.loads(match.group())
    sql = llm.get("sql")
    explication = llm.get("explication", "")
    
    # 4. Si pas de SQL, retourner juste l'explication
    if not sql:
        return {"reponse": explication, "data": [], "sql": None}
    
    # 5. Sécurité : bloquer tout ce qui n'est pas SELECT
    if not sql.strip().upper().startswith("SELECT"):
        return {"reponse": "Requête non autorisée.", "data": [], "sql": None}
    
    # 6. Exécuter le SQL
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(sql)
    data = cursor.fetchall()
    conn.close()
    
    return {
        "reponse": explication,
        "data": data,
        "sql": sql,
        "count": len(data)
    }