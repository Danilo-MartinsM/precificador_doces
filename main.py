from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
from datetime import datetime

DB_FILE = "precificador.db"

app = FastAPI(title="API Precificador de Doces")

# Permitir front-end
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Helpers ---
def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # permite acessar colunas como dict
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # Tabela de ingredientes
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ingredientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            unit TEXT DEFAULT 'g',
            amount REAL DEFAULT 100,
            price REAL DEFAULT 0,
            density REAL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela de receitas
    cur.execute("""
        CREATE TABLE IF NOT EXISTS receitas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            categoria TEXT,
            rendimento REAL DEFAULT 1,
            embalagem REAL DEFAULT 0,
            margem REAL DEFAULT 50,
            custo_total REAL DEFAULT 0,
            preco_sugerido REAL DEFAULT 0,
            preco_por_unidade REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela receita x ingrediente
    cur.execute("""
        CREATE TABLE IF NOT EXISTS receita_ingredientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receita_id INTEGER NOT NULL,
            ingrediente_id INTEGER NOT NULL,
            quantidade REAL DEFAULT 0,
            unidade TEXT DEFAULT 'g',
            FOREIGN KEY(receita_id) REFERENCES receitas(id),
            FOREIGN KEY(ingrediente_id) REFERENCES ingredientes(id)
        )
    """)

    # Tabela de produtos
    cur.execute("""
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            categoria TEXT,
            embalagem REAL DEFAULT 0,
            margem REAL DEFAULT 50,
            rendimento INTEGER DEFAULT 1,
            custo_total REAL DEFAULT 0,
            preco_por_unidade REAL DEFAULT 0
        )
    """)

    # Produto x receita
    cur.execute("""
        CREATE TABLE IF NOT EXISTS produto_receitas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            produto_id INTEGER NOT NULL,
            receita_id INTEGER NOT NULL,
            quantidade REAL DEFAULT 0,
            FOREIGN KEY(produto_id) REFERENCES produtos(id),
            FOREIGN KEY(receita_id) REFERENCES receitas(id)
        )
    """)

    # Produto x ingrediente
    cur.execute("""
        CREATE TABLE IF NOT EXISTS produto_ingredientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            produto_id INTEGER NOT NULL,
            ingrediente_id INTEGER NOT NULL,
            quantidade REAL DEFAULT 0,
            unidade TEXT DEFAULT 'g',
            FOREIGN KEY(produto_id) REFERENCES produtos(id),
            FOREIGN KEY(ingrediente_id) REFERENCES ingredientes(id)
        )
    """)

    conn.commit()
    cur.close()
    conn.close()

# --- Models ---
class IngredienteIn(BaseModel):
    nome: str
    unit: str = "g"
    amount: float = 100.0
    price: float = 0.0
    density: float = 1.0

class IngredienteOut(IngredienteIn):
    id: int
    created_at: Optional[datetime] = None

class ReceitaIngredienteIn(BaseModel):
    ingrediente_id: int
    quantidade: float
    unidade: str = "g"

class ReceitaIn(BaseModel):
    nome: str
    categoria: Optional[str] = None
    rendimento: float = 1.0
    embalagem: float = 0.0
    margem: float = 50.0
    ingredientes: List[ReceitaIngredienteIn] = []

class ProdutoIngredienteIn(BaseModel):
    ingrediente_id: int
    quantidade: float
    unidade: str = "g"

class ProdutoReceitaIn(BaseModel):
    receita_id: int
    quantidade: float

class ProdutoIn(BaseModel):
    nome: str
    categoria: Optional[str] = None
    embalagem: float = 0.0
    margem: float = 50.0
    rendimento: int = 1
    receitas: List[ProdutoReceitaIn] = []
    ingredientes: List[ProdutoIngredienteIn] = []

# ----------------- ROTAS -----------------

# Ingredientes
@app.get("/ingredientes", response_model=List[IngredienteOut])
def list_ingredientes():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM ingredientes ORDER BY nome ASC")
    rows = [dict(row) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return rows

@app.post("/ingredientes", response_model=IngredienteOut)
def create_ingrediente(payload: IngredienteIn):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ingredientes (nome, unit, amount, price, density) VALUES (?,?,?,?,?)",
        (payload.nome, payload.unit, payload.amount, payload.price, payload.density)
    )
    new_id = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    return {**payload.dict(), "id": new_id}

@app.put("/ingredientes/{id}", response_model=IngredienteOut)
def update_ingrediente(id: int, payload: IngredienteIn):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE ingredientes SET nome=?, unit=?, amount=?, price=?, density=? WHERE id=?",
        (payload.nome, payload.unit, payload.amount, payload.price, payload.density, id)
    )
    if cur.rowcount == 0:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Ingrediente não encontrado")
    conn.commit()
    cur.close()
    conn.close()
    return {**payload.dict(), "id": id}

@app.delete("/ingredientes/{id}")
def delete_ingrediente(id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM ingredientes WHERE id=?", (id,))
    affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    if affected == 0:
        raise HTTPException(status_code=404, detail="Ingrediente não encontrado")
    return {"deleted": id}

# Receitas
@app.post("/receitas")
def create_receita(receita: ReceitaIn):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO receitas (nome, categoria, rendimento, embalagem, margem) VALUES (?, ?, ?, ?, ?)",
            (receita.nome, receita.categoria, receita.rendimento, receita.embalagem, receita.margem)
        )
        receita_id = cur.lastrowid
        custo_total = 0.0
        for item in receita.ingredientes:
            cur.execute("SELECT amount, price, unit, density FROM ingredientes WHERE id=?", (item.ingrediente_id,))
            ing = cur.fetchone()
            if not ing: raise HTTPException(status_code=404, detail=f"Ingrediente {item.ingrediente_id} não encontrado")
            amount, price, unit, density = ing
            conv = item.quantidade
            if item.unidade != unit:
                if item.unidade=="ml" and unit=="g": conv = item.quantidade*density
                elif item.unidade=="g" and unit=="ml": conv = item.quantidade/density
                elif item.unidade=="unit" and unit!="unit": conv = item.quantidade*amount
                elif unit=="unit" and item.unidade!="unit": conv = item.quantidade/amount
            custo_item = (conv / amount) * price
            custo_total += custo_item
            cur.execute("INSERT INTO receita_ingredientes (receita_id, ingrediente_id, quantidade, unidade) VALUES (?,?,?,?)",
                        (receita_id, item.ingrediente_id, item.quantidade, item.unidade))
        total_com_embalagem = custo_total + receita.embalagem
        preco_sugerido = total_com_embalagem*(1 + receita.margem/100)
        preco_por_unidade = preco_sugerido / max(receita.rendimento,1)
        cur.execute("UPDATE receitas SET custo_total=?, preco_sugerido=?, preco_por_unidade=? WHERE id=?",
                    (total_com_embalagem, preco_sugerido, preco_por_unidade, receita_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao salvar receita: {str(e)}")
    finally:
        cur.close(); conn.close()
    return {"id": receita_id, "nome": receita.nome, "categoria": receita.categoria,
            "rendimento": receita.rendimento, "custo_total": round(total_com_embalagem,2),
            "preco_sugerido": round(preco_sugerido,2), "preco_por_unidade": round(preco_por_unidade,2),
            "ingredientes": receita.ingredientes}

@app.get("/receitas")
def list_receitas():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM receitas ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        cur2 = conn.cursor()
        cur2.execute("""
            SELECT ri.id, ri.quantidade, ri.unidade, i.id AS ingrediente_id, i.nome, i.unit AS ingrediente_unit, i.price, i.amount, i.density
            FROM receita_ingredientes ri
            JOIN ingredientes i ON i.id = ri.ingrediente_id
            WHERE ri.receita_id=?
        """, (r["id"],))
        r["itens"] = [dict(i) for i in cur2.fetchall()]
        cur2.close()
    cur.close()
    conn.close()
    return rows

@app.put("/receitas/{id}")
def update_receita(id: int, receita: ReceitaIn):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM receitas WHERE id=?", (id,))
    if not cur.fetchone(): cur.close(); conn.close(); raise HTTPException(status_code=404, detail="Receita não encontrada")
    try:
        cur.execute("UPDATE receitas SET nome=?, categoria=?, rendimento=?, embalagem=?, margem=? WHERE id=?",
                    (receita.nome, receita.categoria, receita.rendimento, receita.embalagem, receita.margem, id))
        cur.execute("DELETE FROM receita_ingredientes WHERE receita_id=?", (id,))
        custo_total = 0.0
        for item in receita.ingredientes:
            cur.execute("SELECT amount, price, unit, density FROM ingredientes WHERE id=?", (item.ingrediente_id,))
            ing = cur.fetchone()
            if not ing: raise HTTPException(status_code=404, detail=f"Ingrediente {item.ingrediente_id} não encontrado")
            amount, price, unit, density = ing
            conv = item.quantidade
            if item.unidade != unit:
                if item.unidade=="ml" and unit=="g": conv = item.quantidade*density
                elif item.unidade=="g" and unit=="ml": conv = item.quantidade/density
                elif item.unidade=="unit" and unit!="unit": conv = item.quantidade*amount
                elif unit=="unit" and item.unidade!="unit": conv = item.quantidade/amount
            custo_item = (conv / amount) * price
            custo_total += custo_item
            cur.execute("INSERT INTO receita_ingredientes (receita_id, ingrediente_id, quantidade, unidade) VALUES (?,?,?,?)",
                        (id, item.ingrediente_id, item.quantidade, item.unidade))
        total_com_embalagem = custo_total + receita.embalagem
        preco_sugerido = total_com_embalagem*(1+receita.margem/100)
        preco_por_unidade = preco_sugerido/max(receita.rendimento,1)
        cur.execute("UPDATE receitas SET custo_total=?, preco_sugerido=?, preco_por_unidade=? WHERE id=?",
                    (total_com_embalagem, preco_sugerido, preco_por_unidade, id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar receita: {str(e)}")
    finally:
        cur.close(); conn.close()
    return {"id": id, "nome": receita.nome, "categoria": receita.categoria,
            "rendimento": receita.rendimento, "custo_total": round(total_com_embalagem,2),
            "preco_sugerido": round(preco_sugerido,2), "preco_por_unidade": round(preco_por_unidade,2),
            "ingredientes": receita.ingredientes}

@app.delete("/receitas/{id}")
def delete_receita(id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM receitas WHERE id=?", (id,))
    if not cur.fetchone(): cur.close(); conn.close(); raise HTTPException(status_code=404, detail="Receita não encontrada")
    try:
        cur.execute("DELETE FROM receita_ingredientes WHERE receita_id=?", (id,))
        cur.execute("DELETE FROM receitas WHERE id=?", (id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao excluir receita: {str(e)}")
    finally:
        cur.close(); conn.close()
    return {"deleted": id}

# Produtos
@app.post("/produtos")
def create_produto(produto: ProdutoIn):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO produtos (nome, categoria, embalagem, margem, rendimento) VALUES (?,?,?,?,?)",
                    (produto.nome, produto.categoria, produto.embalagem, produto.margem, produto.rendimento))
        produto_id = cur.lastrowid
        custo_total = 0.0
        for item in produto.receitas:
            cur.execute("SELECT custo_total, rendimento FROM receitas WHERE id=?", (item.receita_id,))
            receita_db = cur.fetchone()
            if not receita_db: raise HTTPException(status_code=404, detail=f"Receita {item.receita_id} não encontrada")
            receita_custo, receita_rendimento = receita_db
            custo_item = (item.quantidade / receita_rendimento) * receita_custo
            custo_total += custo_item
            cur.execute("INSERT INTO produto_receitas (produto_id, receita_id, quantidade) VALUES (?,?,?)",
                        (produto_id, item.receita_id, item.quantidade))
        for item in produto.ingredientes:
            cur.execute("SELECT amount, price, unit, density FROM ingredientes WHERE id=?", (item.ingrediente_id,))
            ing = cur.fetchone()
            if not ing: raise HTTPException(status_code=404, detail=f"Ingrediente {item.ingrediente_id} não encontrado")
            amount, price, unit, density = ing
            conv = item.quantidade
            if item.unidade != unit:
                if item.unidade=="ml" and unit=="g": conv = item.quantidade*density
                elif item.unidade=="g" and unit=="ml": conv = item.quantidade/density
                elif item.unidade=="unit" and unit!="unit": conv = item.quantidade*amount
                elif unit=="unit" and item.unidade!="unit": conv = item.quantidade/amount
            custo_item = (conv / amount) * price
            custo_total += custo_item
            cur.execute("INSERT INTO produto_ingredientes (produto_id, ingrediente_id, quantidade, unidade) VALUES (?,?,?,?)",
                        (produto_id, item.ingrediente_id, item.quantidade, item.unidade))
        total_com_embalagem = custo_total + produto.embalagem
        preco_por_unidade = total_com_embalagem*(1 + produto.margem/100)/max(produto.rendimento,1)
        cur.execute("UPDATE produtos SET custo_total=?, preco_por_unidade=? WHERE id=?",
                    (total_com_embalagem, preco_por_unidade, produto_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao salvar produto: {str(e)}")
    finally:
        cur.close(); conn.close()
    return {"id": produto_id, "nome": produto.nome, "categoria": produto.categoria,
            "rendimento": produto.rendimento, "custo_total": round(total_com_embalagem,2),
            "preco_por_unidade": round(preco_por_unidade,2),
            "receitas": produto.receitas, "ingredientes": produto.ingredientes}

@app.get("/produtos")
def list_produtos():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM produtos ORDER BY id DESC")
    rows = [dict(r) for r in cur.fetchall()]

    for r in rows:
        # receitas do produto (somente resumo)
        cur.execute("""
            SELECT pr.id, pr.quantidade, r.id AS receita_id, r.nome
            FROM produto_receitas pr
            JOIN receitas r ON r.id = pr.receita_id
            WHERE pr.produto_id=?
        """, (r["id"],))
        r["receitas"] = [dict(x) for x in cur.fetchall()]

        # ingredientes diretos do produto (se quiser manter)
        cur.execute("""
            SELECT pi.id, pi.quantidade, pi.unidade, i.id AS ingrediente_id, i.nome
            FROM produto_ingredientes pi
            JOIN ingredientes i ON i.id = pi.ingrediente_id
            WHERE pi.produto_id=?
        """, (r["id"],))
        r["ingredientes"] = [dict(x) for x in cur.fetchall()]

    cur.close()
    conn.close()
    return rows


@app.delete("/produtos/{id}")
def delete_produto(id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM produtos WHERE id=?", (id,))
    if not cur.fetchone(): cur.close(); conn.close(); raise HTTPException(status_code=404, detail="Produto não encontrado")
    try:
        cur.execute("DELETE FROM produto_receitas WHERE produto_id=?", (id,))
        cur.execute("DELETE FROM produto_ingredientes WHERE produto_id=?", (id,))
        cur.execute("DELETE FROM produtos WHERE id=?", (id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao excluir produto: {str(e)}")
    finally:
        cur.close(); conn.close()
    return {"deleted": id}

# Inicializa banco ao iniciar
init_db()
