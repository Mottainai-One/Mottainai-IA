-- ====================================================================
-- Mottainai v6 — massa de desenvolvimento oficial (dataLoad)
-- Fonte oficial: Mottainai-One/Mottainai-Banco-Operacional PR #11
--   commit 473aadba0495acfcaea7d544e4b458b40b962b3d (2026-08-15)
-- ATENÇÃO: este script APAGA (DELETE) os dados transacionais existentes
-- antes de recarregar a massa demo. Use somente em banco local de
-- desenvolvimento, via Apply-PostgresDataLoad.ps1, nunca em dados reais.
-- Única transformação: remoção do BOM (U+FEFF). Conteúdo idêntico à PR.
-- ====================================================================

-- ====================================================================
-- MOTTAINAI - DATA LOAD STARTER v2
-- Objetivo: gerar massa inicial verossímil para validação do banco
-- Escopo: cadastro, catálogo, estoque, vendas, alertas, abastecimento,
--         movimentações, IA e métricas operacionais
-- Versão: 500+ registros por tabela principal
-- ====================================================================

SET search_path TO mottainai;

BEGIN;

-- ====================================================================
-- 0) LIMPEZA (carga idempotente)
-- Remove os dados gerados por execuções anteriores em ordem de FK
-- (filhos antes dos pais), permitindo re-execução do script.
-- ====================================================================

DELETE FROM sale_payment;
DELETE FROM fiscal_document;
DELETE FROM pos_cancel_request;
DELETE FROM sale_item;
DELETE FROM sales_transaction;
DELETE FROM pos_cash_movement;
DELETE FROM pos_shift;
DELETE FROM pos_terminal;
DELETE FROM inventory_movement;
DELETE FROM replenishment_execution_item;
DELETE FROM replenishment_execution;
DELETE FROM replenishment_pre_list_item;
DELETE FROM replenishment_pre_list;
DELETE FROM suggested_action;
DELETE FROM alert;
DELETE FROM transfer_item;
DELETE FROM transfer;
DELETE FROM donation_item;
DELETE FROM donation;
DELETE FROM disposal_item;
DELETE FROM disposal;
DELETE FROM inventory;
DELETE FROM batch;
DELETE FROM supplier_product;
DELETE FROM product;
DELETE FROM app_user;
DELETE FROM employee;
DELETE FROM retail_store;
DELETE FROM company;
DELETE FROM supplier;
DELETE FROM address;
DELETE FROM ai_feedback;
DELETE FROM ai_execution;
DELETE FROM ai_recommendation;
DELETE FROM ai_prediction;
DELETE FROM kpi_cache;
DELETE FROM event_queue;
DELETE FROM query_performance;
DELETE FROM system_log;
DELETE FROM error_log;
DELETE FROM job_log;
DELETE FROM product_history;
DELETE FROM audit_log;

-- ====================================================================
-- 0) RESET DE SEQUENCES (carga idempotente)
-- Após a limpeza, recomeça os IDs do maior valor existente (1 se a
-- tabela estiver vazia), garantindo que as referências numéricas do
-- script funcionem e que o trigger de SKU não gere valores conflitantes.
-- ====================================================================

DO $$
DECLARE
    v_tbl TEXT;
    v_col TEXT;
    v_seq TEXT;
    v_max BIGINT;
BEGIN
    FOR v_tbl, v_col IN
        SELECT c.relname, a.attname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
        WHERE n.nspname = 'mottainai' AND a.attidentity = 'a'
    LOOP
        v_seq := pg_get_serial_sequence('mottainai.' || v_tbl, v_col);
        IF v_seq IS NOT NULL THEN
            EXECUTE format('SELECT COALESCE(MAX(%I), 0) FROM mottainai.%I', v_col, v_tbl) INTO v_max;
            IF v_max > 0 THEN
                PERFORM setval(v_seq, v_max, true);
            ELSE
                PERFORM setval(v_seq, 1, false);
            END IF;
        END IF;
    END LOOP;
END $$;

SET search_path TO mottainai, public;

-- ====================================================================
-- 0) PARTIÇÕES RETROATIVAS
-- Garante partições para os meses passados usados nas datas geradas
-- (o sp_create_future_partitions() do schema só cria do mês atual +6).
-- ====================================================================

DO $$
DECLARE
    v_date DATE;
    v_partition_name TEXT;
    v_schema TEXT := 'mottainai';
    v_tables TEXT[] := ARRAY['inventory_movement', 'sales_transaction', 'audit_log', 'purchase_order'];
    v_table TEXT;
BEGIN
    FOREACH v_table IN ARRAY v_tables
    LOOP
        FOR v_date IN
            SELECT generate_series(
                DATE_TRUNC('month', NOW()) - interval '3 months',
                DATE_TRUNC('month', NOW()) + interval '6 months',
                interval '1 month'
            )::DATE
        LOOP
            v_partition_name := v_table || '_' || TO_CHAR(v_date, 'YYYY_MM');
            EXECUTE format('
                CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.%I
                FOR VALUES FROM (%L) TO (%L)
            ', v_schema, v_partition_name, v_schema, v_table,
               v_date, v_date + interval '1 month');
        END LOOP;
    END LOOP;

    RAISE NOTICE 'Retroactive partitions created (-3 to +6 months)';
END $$;

-- ====================================================================
-- 1) DADOS BASE / CADASTRAIS
-- ====================================================================

INSERT INTO subscription_plan (name, description, price, store_limit, user_limit)
VALUES
    ('Free', 'Plano básico para testes iniciais', 0, 1, 3),
    ('Basic', 'Plano inicial para operação pequena', 99.90, 3, 10),
    ('Professional', 'Plano intermediário para operação em expansão', 299.90, 10, 50),
    ('Enterprise', 'Plano completo para rede com múltiplas lojas', 999.90, 100, 999)
ON CONFLICT (name) DO NOTHING;

-- ====================================================================
-- 1.1) ENDEREÇOS (10 endereços)
-- ====================================================================

INSERT INTO address (zip_code, street, number, complement, neighborhood, city, state)
SELECT v.zip_code, v.street, v.number, v.complement, v.neighborhood, v.city, v.state
FROM (VALUES
    ('01310930', 'Avenida Paulista', '1000', 'Conjunto 101', 'Bela Vista', 'São Paulo', 'SP'),
    ('04538000', 'Rua Funchal', '250', 'Bloco A', 'Vila Olímpia', 'São Paulo', 'SP'),
    ('30140071', 'Avenida do Contorno', '8000', NULL, 'Funcionários', 'Belo Horizonte', 'MG'),
    ('20010000', 'Rua do Ouvidor', '120', NULL, 'Centro', 'Rio de Janeiro', 'RJ'),
    ('80220000', 'Rua XV de Novembro', '500', 'Sala 301', 'Centro', 'Curitiba', 'PR'),
    ('88015600', 'Rua Felipe Schmidt', '100', 'Loja 2', 'Centro', 'Florianópolis', 'SC'),
    ('90010000', 'Avenida Borges de Medeiros', '1500', NULL, 'Centro', 'Porto Alegre', 'RS'),
    ('70040000', 'Setor Comercial Norte', 'Q 4', 'Bloco C', 'Asa Norte', 'Brasília', 'DF'),
    ('57025000', 'Avenida Rui Barbosa', '1000', NULL, 'Centro', 'Maceió', 'AL'),
    ('64000000', 'Avenida Frei Serafim', '2000', NULL, 'Centro', 'Teresina', 'PI')
) AS v(zip_code, street, number, complement, neighborhood, city, state)
WHERE NOT EXISTS (
    SELECT 1 FROM address a WHERE a.zip_code = v.zip_code
);

-- ====================================================================
-- 1.2) EMPRESA
-- ====================================================================

INSERT INTO company (plan_id, official_name, trade_name, cnpj, email, phone)
SELECT sp.plan_id,
       'Mottainai Comércio e Tecnologia Ltda.',
       'Mottainai',
       '73828777326069',
       'contato@mottainai.com',
       '+55 11 99999-0001'
FROM subscription_plan sp
WHERE sp.name = 'Professional'
ON CONFLICT (cnpj) DO NOTHING;

-- ====================================================================
-- 1.3) CARGOS
-- ====================================================================

INSERT INTO employee_role (name, description, permission_level)
VALUES
    ('Administrator', 'Acesso total ao sistema', 100),
    ('Manager', 'Gestão da loja e aprovações', 80),
    ('Supervisor', 'Supervisão operacional', 60),
    ('Operator', 'Operações de caixa e apoio', 40),
    ('Intern', 'Acesso limitado', 20)
ON CONFLICT (name) DO NOTHING;

-- ====================================================================
-- 1.3b) PERFIS FISCAIS (1 perfil padrão)
-- ====================================================================

INSERT INTO tax_profile (
    code, name, description, cfop, icms_cst, icms_rate,
    pis_cst, pis_rate, cofins_cst, cofins_rate
) VALUES (
    'DEFAULT', 'Perfil fiscal padrão', 'Perfil fiscal inicial para testes',
    '5102', '102', 0, '01', 0, '01', 0
)
ON CONFLICT (code) DO NOTHING;

-- ====================================================================
-- 1.4) LOJAS (5 lojas)
-- ====================================================================

INSERT INTO retail_store (company_id, address_id, name, cnpj, email, phone)
SELECT c.company_id, a.address_id, v.store_name, v.cnpj, v.email, v.phone
FROM company c
JOIN (
    VALUES
        ('Mottainai Paulista', '47082281452971', 'paulista@mottainai.com', '+55 11 3000-0001', '01310930'),
        ('Mottainai Centro SP', '42331070777371', 'centrosp@mottainai.com', '+55 11 3000-0002', '04538000'),
        ('Mottainai BH', '72393704949182', 'bh@mottainai.com', '+55 31 3000-0003', '30140071'),
        ('Mottainai RJ', '82369415053645', 'rj@mottainai.com', '+55 21 3000-0004', '20010000'),
        ('Mottainai Curitiba', '19305747965203', 'curitiba@mottainai.com', '+55 41 3000-0005', '80220000')
) AS v(store_name, cnpj, email, phone, zip_code)
ON TRUE
JOIN address a ON a.zip_code = v.zip_code
ON CONFLICT (cnpj) DO NOTHING;

-- ====================================================================
-- 1.5) FUNCIONÁRIOS (25 funcionários)
-- ====================================================================

INSERT INTO employee (store_id, role_id, name, cpf, email, phone)
SELECT s.store_id, r.role_id, v.name, v.cpf, v.email, v.phone
FROM retail_store s
CROSS JOIN LATERAL (
    VALUES
        -- Loja Paulista
        ('Ana Costa', '55111890815', 'ana.costa@mottainai.com', '+55 11 98888-0001', 'Mottainai Paulista', 'Manager'),
        ('Bruno Lima', '58183100279', 'bruno.lima@mottainai.com', '+55 11 98888-0002', 'Mottainai Paulista', 'Supervisor'),
        ('Carla Souza', '44889734937', 'carla.souza@mottainai.com', '+55 11 98888-0003', 'Mottainai Paulista', 'Operator'),
        ('Diego Alves', '37105202661', 'diego.alves@mottainai.com', '+55 11 98888-0004', 'Mottainai Paulista', 'Operator'),
        ('Elena Ferreira', '30411950380', 'elena.ferreira@mottainai.com', '+55 11 98888-0005', 'Mottainai Paulista', 'Intern'),
        -- Loja Centro SP
        ('Fernando Silva', '78298739760', 'fernando.silva@mottainai.com', '+55 11 98888-0006', 'Mottainai Centro SP', 'Manager'),
        ('Gabriela Santos', '02866037138', 'gabriela.santos@mottainai.com', '+55 11 98888-0007', 'Mottainai Centro SP', 'Supervisor'),
        ('Henrique Oliveira', '14170218299', 'henrique.oliveira@mottainai.com', '+55 11 98888-0008', 'Mottainai Centro SP', 'Operator'),
        ('Isabela Rocha', '37866581533', 'isabela.rocha@mottainai.com', '+55 11 98888-0009', 'Mottainai Centro SP', 'Operator'),
        ('João Pedro', '38409140276', 'joao.pedro@mottainai.com', '+55 11 98888-0010', 'Mottainai Centro SP', 'Intern'),
        -- Loja BH
        ('Kelly Sousa', '91596337869', 'kelly.sousa@mottainai.com', '+55 31 98888-0011', 'Mottainai BH', 'Manager'),
        ('Lucas Mendes', '95569363351', 'lucas.mendes@mottainai.com', '+55 31 98888-0012', 'Mottainai BH', 'Supervisor'),
        ('Mariana Lima', '49551697081', 'mariana.lima@mottainai.com', '+55 31 98888-0013', 'Mottainai BH', 'Operator'),
        ('Nicolas Costa', '00538380250', 'nicolas.costa@mottainai.com', '+55 31 98888-0014', 'Mottainai BH', 'Operator'),
        ('Olivia Santos', '69531312761', 'olivia.santos@mottainai.com', '+55 31 98888-0015', 'Mottainai BH', 'Intern'),
        -- Loja RJ
        ('Paulo Henrique', '70074435400', 'paulo.henrique@mottainai.com', '+55 21 98888-0016', 'Mottainai RJ', 'Manager'),
        ('Quintino Silva', '02149874504', 'quintino.silva@mottainai.com', '+55 21 98888-0017', 'Mottainai RJ', 'Supervisor'),
        ('Rafaela Souza', '96661571365', 'rafaela.souza@mottainai.com', '+55 21 98888-0018', 'Mottainai RJ', 'Operator'),
        ('Samuel Oliveira', '49945415310', 'samuel.oliveira@mottainai.com', '+55 21 98888-0019', 'Mottainai RJ', 'Operator'),
        ('Tatiane Rocha', '32576758959', 'tatiane.rocha@mottainai.com', '+55 21 98888-0020', 'Mottainai RJ', 'Intern'),
        -- Loja Curitiba
        ('Ulysses Santos', '22372636472', 'ulysses.santos@mottainai.com', '+55 41 98888-0021', 'Mottainai Curitiba', 'Manager'),
        ('Valentina Lima', '45589372704', 'valentina.lima@mottainai.com', '+55 41 98888-0022', 'Mottainai Curitiba', 'Supervisor'),
        ('Wagner Costa', '30498101908', 'wagner.costa@mottainai.com', '+55 41 98888-0023', 'Mottainai Curitiba', 'Operator'),
        ('Ximena Santos', '31566202701', 'ximena.santos@mottainai.com', '+55 41 98888-0024', 'Mottainai Curitiba', 'Operator'),
        ('Yuri Oliveira', '49741600100', 'yuri.oliveira@mottainai.com', '+55 41 98888-0025', 'Mottainai Curitiba', 'Intern')
) AS v(name, cpf, email, phone, store_name, role_name)
JOIN employee_role r ON r.name = v.role_name
WHERE s.name = v.store_name
ON CONFLICT (cpf) DO NOTHING;

-- ====================================================================
-- 1.6) USUÁRIOS (25 usuários)
-- ====================================================================

INSERT INTO app_user (employee_id, email, password_hash)
SELECT e.employee_id, e.email, crypt('mottainai123', gen_salt('bf'))
FROM employee e
ON CONFLICT (employee_id) DO NOTHING;

-- ====================================================================
-- 2) CATÁLOGO
-- ====================================================================

-- ====================================================================
-- 2.1) CATEGORIAS (12 categorias)
-- ====================================================================

INSERT INTO product_category (name, description)
VALUES
    ('Hortifruti', 'Frutas, legumes e verduras frescas'),
    ('Carnes', 'Carnes bovinas, suínas e aves'),
    ('Laticínios', 'Leites, queijos e derivados'),
    ('Mercearia', 'Alimentos secos e industrializados'),
    ('Bebidas', 'Bebidas refrigeradas e não refrigeradas'),
    ('Limpeza', 'Itens de higiene e limpeza'),
    ('Higiene Pessoal', 'Shampoos, sabonetes e cosméticos'),
    ('Padaria', 'Pães, bolos e produtos de confeitaria'),
    ('Congelados', 'Alimentos congelados em geral'),
    ('Açougue', 'Carnes processadas e embutidos'),
    ('Peixaria', 'Peixes e frutos do mar'),
    ('Grãos', 'Feijão, arroz, lentilha e cereais')
ON CONFLICT (name) DO NOTHING;

-- ====================================================================
-- 2.2) FORNECEDORES (10 fornecedores)
-- ====================================================================

INSERT INTO supplier (address_id, trade_name, cnpj, email, phone)
SELECT a.address_id, v.trade_name, v.cnpj, v.email, v.phone
FROM (
    VALUES
        ('Swift Alimentos', '21623880010004', 'comercial@swift.com', '+55 11 4000-0001', '04538000'),
        ('Sadia Distribuição', '82155190371758', 'comercial@sadia.com', '+55 11 4000-0002', '20010000'),
        ('Nestlé Brasil', '31399733697507', 'comercial@nestle.com', '+55 11 4000-0003', '88015600'),
        ('Pepsico', '81576804366202', 'comercial@pepsico.com', '+55 11 4000-0004', '70040000'),
        ('Coca-Cola Femsa', '85339416338710', 'comercial@cocacola.com', '+55 11 4000-0005', '64000000'),
        ('Aurora Coop', '05906385303908', 'comercial@aurora.com', '+55 11 4000-0006', '01310930'),
        ('JBS Alimentos', '07860331076392', 'comercial@jbs.com', '+55 11 4000-0007', '30140071'),
        ('BRF Foods', '99474207031206', 'comercial@brf.com', '+55 11 4000-0008', '80220000'),
        ('Ambev', '07472610523156', 'comercial@ambev.com', '+55 11 4000-0009', '90010000'),
        ('Unilever Brasil', '32851755012798', 'comercial@unilever.com', '+55 11 4000-0010', '57025000')
) AS v(trade_name, cnpj, email, phone, zip_code)
JOIN address a ON a.zip_code = v.zip_code
ON CONFLICT (cnpj) DO NOTHING;

-- ====================================================================
-- 2.3) PRODUTOS (60 produtos)
-- ====================================================================

DO $$
DECLARE
    v_cat_id INTEGER;
    v_product_names TEXT[] := ARRAY[
        'Banana Prata', 'Maçã Gala', 'Laranja Pera', 'Alface Crespa', 'Tomate Italiano',
        'Cenoura', 'Batata Inglesa', 'Cebola', 'Alho', 'Pimentão Verde',
        'Picanha', 'Contra Filé', 'Alcatra', 'Maminha', 'Fraldinha',
        'Peito de Frango', 'Coxa de Frango', 'Sobrecoxa', 'Linguiça Toscana', 'Bisteca',
        'Leite Integral', 'Queijo Mussarela', 'Queijo Prato', 'Requeijão', 'Iogurte',
        'Manteiga', 'Margarina', 'Creme de Leite', 'Leite Condensado', 'Bebida Láctea',
        'Arroz Branco', 'Feijão Preto', 'Feijão Carioca', 'Lentilha', 'Macarrão',
        'Farinha de Trigo', 'Açúcar Refinado', 'Sal', 'Óleo de Soja', 'Vinagre',
        'Coca-Cola', 'Guaraná Antártica', 'Pepsi', 'Suco de Laranja', 'Água Mineral',
        'Cerveja Pilsen', 'Vinho Tinto', 'Suco de Uva', 'Refrigerante de Limão', 'Energético',
        'Sabão em Pó', 'Detergente', 'Desinfetante', 'Álcool 70', 'Amaciante',
        'Esponja de Aço', 'Pano de Chão', 'Luvas de Limpeza', 'Bucha Vegetal', 'Sacos de Lixo'
    ];
BEGIN
    FOR i IN 1..60 LOOP
        SELECT category_id INTO v_cat_id
        FROM product_category
        ORDER BY category_id
        OFFSET ((i - 1) % 12)
        LIMIT 1;

        INSERT INTO product (category_id, tax_profile_id, barcode, name, description, brand, unit_measure, weight, ncm)
        VALUES (
            v_cat_id,
            (SELECT tax_profile_id FROM tax_profile WHERE code = 'DEFAULT'),
            'MOT' || LPAD(i::TEXT, 8, '0'),
            v_product_names[i],
            'Produto de qualidade Mottainai',
            CASE (i % 5)
                WHEN 0 THEN 'Marca Premium'
                WHEN 1 THEN 'Marca A'
                WHEN 2 THEN 'Marca B'
                WHEN 3 THEN 'Marca C'
                ELSE 'Marca D'
            END,
            CASE (i % 4)
                WHEN 0 THEN 'KG'
                WHEN 1 THEN 'UN'
                WHEN 2 THEN 'L'
                ELSE 'G'
            END,
            (0.500 + (i % 20) * 0.100)::NUMERIC(10,3),
            LPAD((1000 + (i % 7000))::TEXT, 8, '0')
        )
        ON CONFLICT (barcode) DO NOTHING;
    END LOOP;
END $$;

-- ====================================================================
-- 2.4) PRODUTOS POR FORNECEDOR (100+ registros)
-- ====================================================================

INSERT INTO supplier_product (supplier_id, product_id, supplier_code, purchase_price, lead_time)
SELECT 
    s.supplier_id,
    p.product_id,
    'SUP-' || LPAD(p.product_id::TEXT, 4, '0'),
    (2.50 + (p.product_id % 20) * 1.25)::NUMERIC(10,2),
    1 + (p.product_id % 5)
FROM supplier s
CROSS JOIN product p
WHERE (s.supplier_id % 2) = (p.product_id % 2)
  AND p.product_id <= 40
ON CONFLICT (supplier_id, product_id) DO NOTHING;

-- ====================================================================
-- 3) LOTES + ESTOQUE (60 produtos x 2 lotes por loja = 600+ registros)
-- ====================================================================

DO $$
DECLARE
    v_batch_id INTEGER;
    v_store RECORD;
    v_product RECORD;
    v_qty_base NUMERIC(10,3);
    v_cost_base NUMERIC(10,2);
    v_exp_days INTEGER;
BEGIN
    FOR v_store IN SELECT store_id FROM retail_store ORDER BY store_id
    LOOP
        FOR v_product IN SELECT product_id FROM product WHERE product_id <= 60
        LOOP
            -- Lote A
            v_qty_base := 50 + (v_product.product_id % 30);
            v_cost_base := 1.50 + (v_product.product_id % 15) * 0.50;
            v_exp_days := 10 + (v_product.product_id % 40);

            INSERT INTO batch (
                product_id,
                batch_code,
                manufacture_date,
                expiration_date,
                initial_quantity,
                unit_cost
            )
            VALUES (
                v_product.product_id,
                'BATCH-' || LPAD(v_store.store_id::TEXT, 2, '0') || '-' || LPAD(v_product.product_id::TEXT, 4, '0') || '-A',
                CURRENT_DATE - (1 + (v_product.product_id % 20)),
                CURRENT_DATE + v_exp_days,
                v_qty_base + (v_store.store_id * 5),
                v_cost_base + (v_store.store_id * 0.25)
            )
            RETURNING batch_id INTO v_batch_id;

            INSERT INTO inventory (
                store_id,
                batch_id,
                inventory_type,
                current_quantity,
                minimum_quantity,
                maximum_quantity,
                location
            )
            VALUES (
                v_store.store_id,
                v_batch_id,
                'NORMAL',
                v_qty_base + (v_store.store_id * 5) - (v_product.product_id % 5),
                5 + (v_store.store_id * 2),
                150 + (v_store.store_id * 20),
                'Depósito ' || v_store.store_id
            );

            -- Lote B
            v_qty_base := 40 + (v_product.product_id % 25);
            v_cost_base := 1.80 + (v_product.product_id % 12) * 0.45;
            v_exp_days := 25 + (v_product.product_id % 50);

            INSERT INTO batch (
                product_id,
                batch_code,
                manufacture_date,
                expiration_date,
                initial_quantity,
                unit_cost
            )
            VALUES (
                v_product.product_id,
                'BATCH-' || LPAD(v_store.store_id::TEXT, 2, '0') || '-' || LPAD(v_product.product_id::TEXT, 4, '0') || '-B',
                CURRENT_DATE - (5 + (v_product.product_id % 15)),
                CURRENT_DATE + v_exp_days,
                v_qty_base + (v_store.store_id * 4),
                v_cost_base + (v_store.store_id * 0.20)
            )
            RETURNING batch_id INTO v_batch_id;

            INSERT INTO inventory (
                store_id,
                batch_id,
                inventory_type,
                current_quantity,
                minimum_quantity,
                maximum_quantity,
                location
            )
            VALUES (
                v_store.store_id,
                v_batch_id,
                'NORMAL',
                v_qty_base + (v_store.store_id * 4) - (v_product.product_id % 3),
                5 + (v_store.store_id * 2),
                150 + (v_store.store_id * 20),
                'Câmara-fria ' || v_store.store_id
            );
        END LOOP;
    END LOOP;
END $$;

-- ====================================================================
-- 4) VENDAS (500+ vendas)
-- ====================================================================

DO $$
DECLARE
    v_sale_id INTEGER;
    v_sale_dt TIMESTAMP;
    v_store RECORD;
    v_employee RECORD;
    v_product_id INTEGER;
    v_qty NUMERIC(10,3);
    v_unit_price NUMERIC(10,2);
    v_batch_id INTEGER;
    v_sale_count INTEGER := 0;
    v_payment_methods payment_method[] := ARRAY['CASH'::payment_method, 'CARD'::payment_method, 'PIX'::payment_method];
    v_payment_method payment_method;
    v_products INTEGER[];
    v_shift_id INTEGER;
BEGIN
    -- Cria array de produtos
    SELECT ARRAY(SELECT product_id FROM product) INTO v_products;

    FOR v_store IN SELECT store_id FROM retail_store ORDER BY store_id
    LOOP
        -- Cria terminal e turno (shift) da loja para atender a FK de sales_transaction
        INSERT INTO pos_terminal (store_id, terminal_code, name, hostname)
        VALUES (v_store.store_id, 'PDV-' || v_store.store_id, 'Caixa Principal ' || v_store.store_id, 'host-pdv-' || v_store.store_id)
        ON CONFLICT (store_id, terminal_code) DO NOTHING;

        INSERT INTO pos_shift (terminal_id, employee_id, opened_at, opening_amount, status)
        SELECT t.terminal_id, e.employee_id, CURRENT_TIMESTAMP - interval '1 day', 100.00, 'OPEN'
        FROM pos_terminal t
        JOIN employee e ON e.store_id = t.store_id AND e.role_id = (SELECT role_id FROM employee_role WHERE name = 'Manager')
        WHERE t.store_id = v_store.store_id
        LIMIT 1
        RETURNING shift_id INTO v_shift_id;

        IF v_shift_id IS NULL THEN
            SELECT ps.shift_id INTO v_shift_id
            FROM pos_shift ps
            JOIN pos_terminal t ON t.terminal_id = ps.terminal_id
            WHERE t.store_id = v_store.store_id
            LIMIT 1;
        END IF;

        SELECT employee_id INTO v_employee
        FROM employee
        WHERE store_id = v_store.store_id
          AND role_id = (SELECT role_id FROM employee_role WHERE name = 'Operator')
        ORDER BY random()
        LIMIT 1;

        -- Gera 100+ vendas por loja (total 500+)
        FOR i IN 1..120 LOOP
            v_sale_dt := CURRENT_TIMESTAMP - (random() * interval '30 days');
            v_product_id := v_products[1 + floor(random() * array_length(v_products, 1))::INTEGER];
            v_qty := 1 + floor(random() * 4);
            v_unit_price := (3.99 + random() * 25.00)::NUMERIC(10,2);
            v_payment_method := v_payment_methods[1 + floor(random() * array_length(v_payment_methods, 1))::INTEGER];

            INSERT INTO sales_transaction (
                store_id, employee_id, shift_id, sale_date, total_amount, status, observation
            )
            VALUES (
                v_store.store_id,
                v_employee.employee_id,
                v_shift_id,
                v_sale_dt,
                (v_qty * v_unit_price)::NUMERIC(12,2),
                CASE WHEN random() > 0.95 THEN 'CANCELED'::sale_status ELSE 'COMPLETED'::sale_status END,
                'Venda ' || i || ' - Loja ' || v_store.store_id
            )
            RETURNING sale_id, sale_date INTO v_sale_id, v_sale_dt;

            INSERT INTO sale_item (
                sale_id, product_id, batch_id, quantity_sold, unit_price, sale_date
            )
            VALUES (
                v_sale_id,
                v_product_id,
                NULL,
                v_qty,
                v_unit_price,
                v_sale_dt
            );

            v_sale_count := v_sale_count + 1;

            -- Itens adicionais (2 itens por venda)
            FOR j IN 1..2 LOOP
                v_product_id := v_products[1 + floor(random() * array_length(v_products, 1))::INTEGER];
                v_qty := 1 + floor(random() * 3);
                v_unit_price := (2.99 + random() * 18.00)::NUMERIC(10,2);

                INSERT INTO sale_item (
                    sale_id, product_id, batch_id, quantity_sold, unit_price, sale_date
                )
                VALUES (
                    v_sale_id,
                    v_product_id,
                    NULL,
                    v_qty,
                    v_unit_price,
                    v_sale_dt
                );
            END LOOP;

            -- Atualiza total
            UPDATE sales_transaction
            SET total_amount = (
                SELECT COALESCE(SUM(subtotal), 0)
                FROM sale_item
                WHERE sale_id = v_sale_id
            )
            WHERE sale_id = v_sale_id;

            -- Registra pagamento da venda
            INSERT INTO sale_payment (sale_id, sale_date, payment_method, amount, installments, paid_at)
            SELECT v_sale_id, v_sale_dt, v_payment_method,
                   COALESCE(SUM(subtotal), 0), 1, v_sale_dt
            FROM sale_item
            WHERE sale_id = v_sale_id;
        END LOOP;
    END LOOP;
END $$;

-- ====================================================================
-- 5) MOVIMENTAÇÕES (500+ movimentações)
-- ====================================================================

DO $$
DECLARE
    v_inventory RECORD;
    v_employee_id INTEGER;
    v_prev NUMERIC(10,3);
    v_new NUMERIC(10,3);
    v_qty NUMERIC(10,3);
    v_type movement_type;
    v_count INTEGER := 0;
BEGIN
    SELECT employee_id INTO v_employee_id
    FROM employee
    WHERE name = 'Bruno Lima';

    FOR v_inventory IN SELECT inventory_id, store_id, current_quantity FROM inventory LIMIT 300
    LOOP
        FOR i IN 1..3 LOOP
            v_prev := v_inventory.current_quantity;

            IF random() > 0.6 THEN
                v_qty := 1 + floor(random() * 5);
                v_type := 'IN';
            ELSE
                v_qty := -1 * (1 + floor(random() * 3));
                v_type := 'OUT';
            END IF;

            v_new := GREATEST(0, v_prev + v_qty);

            UPDATE inventory
            SET current_quantity = v_new,
                updated_at = NOW(),
                version = version + 1
            WHERE inventory_id = v_inventory.inventory_id;

            INSERT INTO inventory_movement (
                inventory_id, employee_id, movement_date, movement_type,
                moved_quantity, previous_balance, current_balance, observation, store_id
            )
            VALUES (
                v_inventory.inventory_id,
                v_employee_id,
                CURRENT_TIMESTAMP - (random() * interval '30 days'),
                v_type,
                v_qty,
                v_prev,
                v_new,
                'Movimentação automática',
                v_inventory.store_id
            );

            v_count := v_count + 1;
            EXIT WHEN v_count >= 500;
        END LOOP;
        EXIT WHEN v_count >= 500;
    END LOOP;
END $$;

-- ====================================================================
-- 6) ALERTAS + AÇÕES SUGERIDAS (100+ alertas)
-- ====================================================================

DO $$
DECLARE
    v_alert_id INTEGER;
    v_store_id INTEGER;
    v_generated_at TIMESTAMP;
    v_alert_types alert_type[] := ARRAY['RUPTURE'::alert_type, 'EXPIRATION'::alert_type, 'CRITICAL_STOCK'::alert_type, 'SLOW_MOVING'::alert_type];
    v_priorities priority_level[] := ARRAY['LOW'::priority_level, 'MEDIUM'::priority_level, 'HIGH'::priority_level, 'CRITICAL'::priority_level];
    v_status alert_status[] := ARRAY['ACTIVE'::alert_status, 'RESOLVED'::alert_status, 'IGNORED'::alert_status];
BEGIN
    FOR v_store_id IN SELECT store_id FROM retail_store
    LOOP
        FOR i IN 1..25 LOOP
            v_generated_at := CURRENT_TIMESTAMP - (random() * interval '15 days');

            INSERT INTO alert (
                store_id, title, description, alert_type, priority, status, generated_at, resolved_at
            )
            VALUES (
                v_store_id,
                CASE (i % 4)
                    WHEN 0 THEN 'Ruptura iminente: Produto ' || i
                    WHEN 1 THEN 'Vencimento próximo: Lote ' || i
                    WHEN 2 THEN 'Estoque crítico: Produto ' || i
                    ELSE 'Baixo giro: Produto ' || i
                END,
                'Alerta gerado pelo sistema Mottainai',
                v_alert_types[1 + (i % array_length(v_alert_types, 1))],
                v_priorities[1 + (i % array_length(v_priorities, 1))],
                CASE WHEN i % 3 = 0 THEN 'RESOLVED'::alert_status ELSE 'ACTIVE'::alert_status END,
                v_generated_at,
                CASE WHEN i % 3 = 0 THEN v_generated_at + (random() * interval '3 days') ELSE NULL END
            )
            RETURNING alert_id INTO v_alert_id;

            IF random() > 0.3 THEN
                INSERT INTO suggested_action (
                    alert_id, action_type, description, priority, status, generated_at
                )
                VALUES (
                    v_alert_id,
                    CASE (i % 5)
                        WHEN 0 THEN 'REORDER'::suggested_action_type
                        WHEN 1 THEN 'PROMOTION'::suggested_action_type
                        WHEN 2 THEN 'TRANSFER'::suggested_action_type
                        WHEN 3 THEN 'DONATION'::suggested_action_type
                        ELSE 'DISPOSAL'::suggested_action_type
                    END,
                    'Ação sugerida automaticamente',
                    v_priorities[1 + (i % array_length(v_priorities, 1))],
                    CASE WHEN i % 4 = 0 THEN 'EXECUTED'::suggested_action_status ELSE 'PENDING'::suggested_action_status END,
                    CURRENT_TIMESTAMP - (random() * interval '10 days')
                );
            END IF;
        END LOOP;
    END LOOP;
END $$;

-- ====================================================================
-- 7) PRÉ-LISTAS + EXECUÇÃO (100+ pré-listas)
-- ====================================================================

DO $$
DECLARE
    v_pre_list_id INTEGER;
    v_exec_id INTEGER;
    v_store_id INTEGER;
    v_employee_id INTEGER;
    v_products INTEGER[];
    v_start_date TIMESTAMP;
BEGIN
    SELECT ARRAY(SELECT product_id FROM product) INTO v_products;

    FOR v_store_id IN SELECT store_id FROM retail_store
    LOOP
        SELECT employee_id INTO v_employee_id
        FROM employee
        WHERE store_id = v_store_id
        LIMIT 1;

        FOR i IN 1..25 LOOP
            INSERT INTO replenishment_pre_list (store_id, employee_id, generated_at, status)
            VALUES (
                v_store_id,
                v_employee_id,
                CURRENT_TIMESTAMP - (random() * interval '30 days'),
                CASE (i % 4)
                    WHEN 0 THEN 'COMPLETED'::pre_list_status
                    WHEN 1 THEN 'IN_PROGRESS'::pre_list_status
                    ELSE 'GENERATED'::pre_list_status
                END
            )
            RETURNING pre_list_id INTO v_pre_list_id;

            -- Itens da pré-lista
            FOR j IN 1..5 LOOP
                INSERT INTO replenishment_pre_list_item (pre_list_id, product_id, suggested_quantity, priority)
                VALUES (
                    v_pre_list_id,
                    v_products[1 + floor(random() * array_length(v_products, 1))::INTEGER],
                    10 + floor(random() * 20),
                    CASE (j % 3)
                        WHEN 0 THEN 'HIGH'::priority_level
                        WHEN 1 THEN 'MEDIUM'::priority_level
                        ELSE 'LOW'::priority_level
                    END
                );
            END LOOP;

            -- Execução (70% das pré-listas)
            IF random() > 0.3 THEN
                v_start_date := CURRENT_TIMESTAMP - (random() * interval '20 days');

                INSERT INTO replenishment_execution (
                    pre_list_id, employee_id, start_date, end_date, rating, comment
                )
                VALUES (
                    v_pre_list_id,
                    v_employee_id,
                    v_start_date,
                    v_start_date + (random() * interval '5 days'),
                    3 + floor(random() * 3),
                    'Execução com avaliação positiva'
                )
                RETURNING execution_id INTO v_exec_id;

                -- Itens executados
                INSERT INTO replenishment_execution_item (execution_id, batch_id, replenished_quantity)
                SELECT
                    v_exec_id,
                    b.batch_id,
                    5 + floor(random() * 15)
                FROM batch b
                WHERE b.product_id = v_products[1 + floor(random() * array_length(v_products, 1))::INTEGER]
                LIMIT 3;
            END IF;
        END LOOP;
    END LOOP;
END $$;

-- ====================================================================
-- 8) TRANSFERÊNCIAS (30+ transferências)
-- ====================================================================

DO $$
DECLARE
    v_transfer_id INTEGER;
    v_store_pair RECORD;
    v_employee_id INTEGER;
    v_batch_id INTEGER;
    v_products INTEGER[];
BEGIN
    SELECT ARRAY(SELECT product_id FROM product) INTO v_products;

    FOR i IN 1..30 LOOP
        SELECT employee_id INTO v_employee_id
        FROM employee
        ORDER BY random()
        LIMIT 1;

        INSERT INTO transfer (
            source_store_id,
            destination_store_id,
            employee_id,
            request_date,
            completion_date,
            status,
            observation
        )
        SELECT
            s1.store_id,
            s2.store_id,
            v_employee_id,
            CURRENT_TIMESTAMP - (random() * interval '15 days'),
            CASE WHEN random() > 0.2 THEN CURRENT_TIMESTAMP - (random() * interval '5 days') ELSE NULL END,
            CASE WHEN random() > 0.2 THEN 'COMPLETED'::transfer_status ELSE 'REQUESTED'::transfer_status END,
            'Transferência automática ' || i
        FROM retail_store s1
        CROSS JOIN retail_store s2
        WHERE s1.store_id != s2.store_id
        ORDER BY random()
        LIMIT 1
        RETURNING transfer_id INTO v_transfer_id;

        -- Itens da transferência
        FOR j IN 1..3 LOOP
            SELECT batch_id INTO v_batch_id
            FROM batch
            WHERE product_id = v_products[1 + floor(random() * array_length(v_products, 1))::INTEGER]
            LIMIT 1;

            INSERT INTO transfer_item (transfer_id, batch_id, transferred_quantity)
            VALUES (v_transfer_id, v_batch_id, 5 + floor(random() * 15));
        END LOOP;
    END LOOP;
END $$;

-- ====================================================================
-- 9) DOAÇÕES E DESCARTES (30+ registros)
-- ====================================================================

DO $$
DECLARE
    v_donation_id INTEGER;
    v_disposal_id INTEGER;
    v_store_id INTEGER;
    v_employee_id INTEGER;
    v_batch_id INTEGER;
    v_products INTEGER[];
BEGIN
    SELECT ARRAY(SELECT product_id FROM product) INTO v_products;

    FOR i IN 1..20 LOOP
        SELECT store_id, employee_id INTO v_store_id, v_employee_id
        FROM employee
        ORDER BY random()
        LIMIT 1;

        INSERT INTO donation (
            store_id, employee_id, institution, donation_date, status, observation
        )
        VALUES (
            v_store_id,
            v_employee_id,
            'Instituição de Caridade ' || i,
            CURRENT_TIMESTAMP - (random() * interval '20 days'),
            'COMPLETED',
            'Doação automática'
        )
        RETURNING donation_id INTO v_donation_id;

        FOR j IN 1..2 LOOP
            SELECT batch_id INTO v_batch_id
            FROM batch
            WHERE product_id = v_products[1 + floor(random() * array_length(v_products, 1))::INTEGER]
            LIMIT 1;

            INSERT INTO donation_item (donation_id, batch_id, donated_quantity)
            VALUES (v_donation_id, v_batch_id, 5 + floor(random() * 10));
        END LOOP;
    END LOOP;

    -- Descarte
    FOR i IN 1..15 LOOP
        SELECT store_id, employee_id INTO v_store_id, v_employee_id
        FROM employee
        ORDER BY random()
        LIMIT 1;

        INSERT INTO disposal (
            store_id, employee_id, reason, disposal_date, observation
        )
        VALUES (
            v_store_id,
            v_employee_id,
            CASE (i % 3)
                WHEN 0 THEN 'Produto vencido'
                WHEN 1 THEN 'Produto danificado'
                ELSE 'Validade expirada'
            END,
            CURRENT_TIMESTAMP - (random() * interval '10 days'),
            'Descarte automático'
        )
        RETURNING disposal_id INTO v_disposal_id;

        FOR j IN 1..2 LOOP
            SELECT batch_id INTO v_batch_id
            FROM batch
            WHERE product_id = v_products[1 + floor(random() * array_length(v_products, 1))::INTEGER]
            LIMIT 1;

            INSERT INTO disposal_item (disposal_id, batch_id, disposed_quantity)
            VALUES (v_disposal_id, v_batch_id, 3 + floor(random() * 8));
        END LOOP;
    END LOOP;
END $$;

-- ====================================================================
-- 10) IA, KPIs E MONITORAMENTO
-- ====================================================================

INSERT INTO ai_model (name, version, model_type, description, parameters, accuracy)
VALUES
    ('DemandForecast', '1.0.0', 'FORECAST', 'Previsão de demanda', '{"window": 30}', 92.50),
    ('ReplenishmentOptimizer', '1.0.0', 'OPTIMIZATION', 'Otimização de pré-lista', '{"safety_stock": 1.2}', 90.00),
    ('InventoryClassifier', '1.0.0', 'CLASSIFICATION', 'Classificação de criticidade', '{"criticality": "high"}', 88.75),
    ('DemandForecast', '2.0.0', 'FORECAST', 'Previsão de demanda v2', '{"window": 45, "confidence": 0.95}', 94.20),
    ('ReplenishmentOptimizer', '2.0.0', 'OPTIMIZATION', 'Otimização de pré-lista v2', '{"safety_stock": 1.5, "lead_time": 5}', 92.50)
ON CONFLICT (name, version) DO NOTHING;

DO $$
DECLARE
    v_model_id INTEGER;
    v_product_id INTEGER;
    v_store_id INTEGER;
    v_prediction_id INTEGER;
BEGIN
    FOR v_model_id IN SELECT model_id FROM ai_model WHERE active = TRUE
    LOOP
        FOR v_store_id IN SELECT store_id FROM retail_store
        LOOP
            FOR v_product_id IN SELECT product_id FROM product LIMIT 30
            LOOP
                INSERT INTO ai_prediction (
                    model_id, product_id, store_id, predicted_date, confidence,
                    predicted_quantity, actual_quantity
                )
                VALUES (
                    v_model_id,
                    v_product_id,
                    v_store_id,
                    CURRENT_DATE + (floor(random() * 7)::INTEGER + 1),
                    75 + random() * 20,
                    10 + floor(random() * 30),
                    8 + floor(random() * 25)
                )
                RETURNING prediction_id INTO v_prediction_id;

                -- Recomendação para 30% das previsões
                IF random() > 0.7 THEN
                    INSERT INTO ai_recommendation (
                        prediction_id, action_type, priority, reason, status
                    )
                    VALUES (
                        v_prediction_id,
                        CASE WHEN random() > 0.5 THEN 'REORDER'::suggested_action_type ELSE 'PROMOTION'::suggested_action_type END,
                        CASE floor(random() * 4)
                            WHEN 0 THEN 'LOW'::priority_level
                            WHEN 1 THEN 'MEDIUM'::priority_level
                            WHEN 2 THEN 'HIGH'::priority_level
                            ELSE 'CRITICAL'::priority_level
                        END,
                        'Recomendação automática baseada na previsão',
                        'PENDING'
                    );
                END IF;
            END LOOP;
        END LOOP;
    END LOOP;
END $$;

-- ====================================================================
-- 11) FEEDBACK E EXECUÇÃO DE IA
-- ====================================================================

DO $$
DECLARE
    v_user_id INTEGER;
    v_recommendation_id INTEGER;
BEGIN
    SELECT user_id INTO v_user_id FROM app_user LIMIT 1;

    FOR v_recommendation_id IN SELECT recommendation_id FROM ai_recommendation LIMIT 50
    LOOP
        IF random() > 0.4 THEN
            INSERT INTO ai_feedback (recommendation_id, user_id, rating, comment)
            VALUES (
                v_recommendation_id,
                v_user_id,
                3 + floor(random() * 3),
                CASE floor(random() * 4)
                    WHEN 0 THEN 'Recomendação útil'
                    WHEN 1 THEN 'Poderia ser mais precisa'
                    WHEN 2 THEN 'Excelente sugestão'
                    ELSE 'Implementamos com sucesso'
                END
            );
        END IF;

        IF random() > 0.6 THEN
            INSERT INTO ai_execution (recommendation_id, result, success)
            VALUES (
                v_recommendation_id,
                jsonb_build_object('executed', true, 'source', 'automation', 'timestamp', NOW()),
                random() > 0.1
            );
        END IF;
    END LOOP;
END $$;

-- ====================================================================
-- 12) KPI CACHE (para todas as lojas)
-- ====================================================================

DO $$
DECLARE
    v_store_id INTEGER;
    v_kpi_names TEXT[] := ARRAY[
        'stock_coverage', 'active_alerts', 'monthly_revenue', 'critical_products',
        'replenishment_accuracy', 'avg_daily_sales', 'inventory_turnover'
    ];
    v_store_names TEXT[] := ARRAY['Paulista', 'Centro SP', 'BH', 'RJ', 'Curitiba'];
BEGIN
    FOR v_store_id IN SELECT store_id FROM retail_store
    LOOP
        FOR i IN 1..array_length(v_kpi_names, 1) LOOP
            INSERT INTO kpi_cache (store_id, kpi_name, kpi_value, calculated_at, expires_at)
            VALUES (
                v_store_id,
                v_kpi_names[i],
                jsonb_build_object(
                    'value', 10 + floor(random() * 200),
                    'store_name', v_store_names[v_store_id],
                    'timestamp', NOW()
                ),
                NOW(),
                NOW() + INTERVAL '1 day'
            )
            ON CONFLICT (store_id, kpi_name) DO UPDATE
            SET kpi_value = EXCLUDED.kpi_value,
                calculated_at = EXCLUDED.calculated_at,
                expires_at = EXCLUDED.expires_at;
        END LOOP;
    END LOOP;
END $$;

-- ====================================================================
-- 13) PERFORMANCE E LOGS
-- ====================================================================

INSERT INTO query_performance (function_name, execution_time_ms, rows_affected, parameters)
SELECT
    function_name,
    5 + floor(random() * 100),
    1 + floor(random() * 50),
    jsonb_build_object('seed', true, 'timestamp', NOW())
FROM (
    VALUES
        ('fn_atomic_update_inventory'),
        ('fn_select_batch_fefo'),
        ('fn_calculate_average_consumption'),
        ('fn_calculate_coverage'),
        ('fn_calculate_criticality'),
        ('fn_calculate_economy'),
        ('fn_publish_event'),
        ('fn_check_rate_limit'),
        ('fn_calculate_reorder_quantity'),
        ('fn_calculate_replenishment_priority')
) AS funcs(function_name)
CROSS JOIN generate_series(1, 5);

INSERT INTO system_log (log_level, module, message)
SELECT 
    CASE (i % 4)
        WHEN 0 THEN 'INFO'::log_level
        WHEN 1 THEN 'WARN'::log_level
        WHEN 2 THEN 'DEBUG'::log_level
        ELSE 'ERROR'::log_level
    END,
    CASE (i % 5)
        WHEN 0 THEN 'SYSTEM'
        WHEN 1 THEN 'INVENTORY'
        WHEN 2 THEN 'SALES'
        WHEN 3 THEN 'AI'
        ELSE 'ANALYTICS'
    END,
    'Log automático ' || i || ' gerado na carga inicial'
FROM generate_series(1, 50) AS s(i);

INSERT INTO error_log (error_code, error_message, function_name, parameters)
SELECT 
    'ERR-' || LPAD(i::TEXT, 4, '0'),
    'Erro simulado ' || i || ' para testes',
    CASE (i % 3)
        WHEN 0 THEN 'fn_atomic_update_inventory'
        WHEN 1 THEN 'fn_select_batch_fefo'
        ELSE 'fn_calculate_economy'
    END,
    jsonb_build_object('test', true, 'error_code', i)
FROM generate_series(1, 20) AS s(i);

INSERT INTO job_log (job_name, job_type, start_time, end_time, duration_seconds, records_processed, success, details)
SELECT
    job_name,
    'SCHEDULED',
    start_time,
    end_time,
    EXTRACT(EPOCH FROM (end_time - start_time))::INTEGER,
    100 + floor(random() * 900),
    random() > 0.1,
    jsonb_build_object('records', 100 + floor(random() * 900), 'status', 'completed')
FROM (
    VALUES
        ('daily_ai_routine', NOW() - INTERVAL '1 day', NOW()),
        ('sp_check_expiration', NOW() - INTERVAL '1 day', NOW()),
        ('sp_refresh_dashboard', NOW() - INTERVAL '12 hours', NOW()),
        ('sp_clean_kpi_cache', NOW() - INTERVAL '6 hours', NOW()),
        ('sp_archive_old_data', NOW() - INTERVAL '1 week', NOW())
) AS jobs(job_name, start_time, end_time);

-- ====================================================================
-- 14) EVENTOS (100+ eventos)
-- ====================================================================

DO $$
DECLARE
    v_event_types TEXT[] := ARRAY[
        'SALE_COMPLETED', 'RECEIVING_COMPLETED', 'TRANSFER_COMPLETED',
        'PRE_LIST_GENERATED', 'EXPIRATION_CHECK_COMPLETED', 'DAILY_ROUTINE_COMPLETED'
    ];
BEGIN
    FOR i IN 1..100 LOOP
        INSERT INTO event_queue (
            event_type, event_data, priority, status, created_at, processed_at
        )
        VALUES (
            v_event_types[1 + (i % array_length(v_event_types, 1))],
            jsonb_build_object(
                'event_id', i,
                'timestamp', NOW(),
                'source', 'data_load',
                'details', 'Evento automático ' || i
            ),
            1 + floor(random() * 5),
            CASE WHEN random() > 0.3 THEN 'COMPLETED'::event_status ELSE 'PENDING'::event_status END,
            CURRENT_TIMESTAMP - (random() * interval '5 days'),
            CASE WHEN random() > 0.3 THEN CURRENT_TIMESTAMP - (random() * interval '1 day') ELSE NULL END
        );
    END LOOP;
END $$;

-- ====================================================================
-- 15) RESUMO DA CARGA
-- ====================================================================

DO $$
DECLARE
    v_counts RECORD;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'RESUMO DA CARGA DE DADOS';
    RAISE NOTICE '========================================';

    FOR v_counts IN
        SELECT 'address' AS tabela, COUNT(*) AS total FROM address
        UNION ALL SELECT 'company', COUNT(*) FROM company
        UNION ALL SELECT 'retail_store', COUNT(*) FROM retail_store
        UNION ALL SELECT 'employee', COUNT(*) FROM employee
        UNION ALL SELECT 'app_user', COUNT(*) FROM app_user
        UNION ALL SELECT 'product', COUNT(*) FROM product
        UNION ALL SELECT 'supplier', COUNT(*) FROM supplier
        UNION ALL SELECT 'supplier_product', COUNT(*) FROM supplier_product
        UNION ALL SELECT 'batch', COUNT(*) FROM batch
        UNION ALL SELECT 'inventory', COUNT(*) FROM inventory
        UNION ALL SELECT 'sales_transaction', COUNT(*) FROM sales_transaction
        UNION ALL SELECT 'sale_item', COUNT(*) FROM sale_item
        UNION ALL SELECT 'inventory_movement', COUNT(*) FROM inventory_movement
        UNION ALL SELECT 'alert', COUNT(*) FROM alert
        UNION ALL SELECT 'suggested_action', COUNT(*) FROM suggested_action
        UNION ALL SELECT 'replenishment_pre_list', COUNT(*) FROM replenishment_pre_list
        UNION ALL SELECT 'replenishment_pre_list_item', COUNT(*) FROM replenishment_pre_list_item
        UNION ALL SELECT 'replenishment_execution', COUNT(*) FROM replenishment_execution
        UNION ALL SELECT 'replenishment_execution_item', COUNT(*) FROM replenishment_execution_item
        UNION ALL SELECT 'transfer', COUNT(*) FROM transfer
        UNION ALL SELECT 'transfer_item', COUNT(*) FROM transfer_item
        UNION ALL SELECT 'donation', COUNT(*) FROM donation
        UNION ALL SELECT 'donation_item', COUNT(*) FROM donation_item
        UNION ALL SELECT 'disposal', COUNT(*) FROM disposal
        UNION ALL SELECT 'disposal_item', COUNT(*) FROM disposal_item
        UNION ALL SELECT 'ai_prediction', COUNT(*) FROM ai_prediction
        UNION ALL SELECT 'ai_recommendation', COUNT(*) FROM ai_recommendation
        UNION ALL SELECT 'ai_feedback', COUNT(*) FROM ai_feedback
        UNION ALL SELECT 'event_queue', COUNT(*) FROM event_queue
    LOOP
        RAISE NOTICE '%-: % registros', v_counts.tabela, v_counts.total;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'CARGA CONCLUÍDA COM SUCESSO, já da para ver mais um ep de pucca!';
    RAISE NOTICE '========================================';
END $$;

COMMIT;

-- ====================================================================
-- Resumo esperado após a carga - se der certo :
-- - 10 endereços
-- - 1 empresa
-- - 5 lojas
-- - 25 funcionários + 25 usuários
-- - 12 categorias + 60 produtos
-- - 10 fornecedores + 100+ produtos por fornecedor
-- - 600+ lotes + 600+ registros de estoque
-- - 600+ vendas + 1800+ itens de venda
-- - 500+ movimentações de estoque
-- - 125+ alertas + ações sugeridas
-- - 125+ pré-listas + execução
-- - 30+ transferências
-- - 20+ doações
-- - 15+ descartes
-- - IA, KPI, logs e monitoramento
-- - 100+ eventos
-- ====================================================================
