# Open ADE — perintah yang sering dipakai.
#
# Alasan berkas ini ada: perintah-perintah di bawah sebelumnya hanya hidup di kepala satu
# orang dan di riwayat shell. Kriteria penilaian magang (briefing hlm. 21) menyebut
# "orang lain bisa menjalankan dan mengubahnya tanpa bertanya ke kalian" -- ini bagian
# termurah dari janji itu.
#
#     make            daftar perintah
#     make test       jalankan seluruh tes
#     make serve      jalankan API

PY := .venv311/bin/python
PIP := .venv311/bin/pip
API ?= http://localhost:8000
DOC_TYPE ?= auto

# app/config.py tidak memuat .env sendiri. Tanpa ini `make serve` jalan dengan
# NOCODB_PUSH_ENABLED=0 dan token kosong walau .env sudah diisi.
LOAD_ENV := set -a; [ ! -f .env ] || . ./.env; set +a;

.DEFAULT_GOAL := bantuan
.PHONY: bantuan pasang pasang-dev test test-cepat eval serve dok buat-base cek-nocodb cek-skema \
	cek-ocr cek-rahasia rapi cek-gaya uji-jobs bersih

bantuan:  ## Tampilkan daftar perintah ini
	@echo "Open ADE — perintah yang tersedia:"
	@echo
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo

# --------------------------------------------------------------------------- penyiapan
pasang:  ## Pasang dependensi aplikasi + skema dol-schema dari folder sebelah
	$(PIP) install -r requirements.txt
	@test -d ../dol-schema || (echo "✗ ../dol-schema tidak ada: clone repo dol-schema sejajar folder ini"; exit 1)
	$(PIP) install -e ../dol-schema

pasang-dev:  ## Pasang dependensi aplikasi + pengembangan (pytest, ruff)
	$(PIP) install -r requirements-dev.txt

# --------------------------------------------------------------------------- pemeriksaan
rapi:  ## Rapikan kode sesuai PEP 8 (ruff: perbaiki + format)
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

cek-gaya:  ## Periksa gaya kode tanpa mengubah apa pun (dipakai CI)
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

test:  ## Jalankan seluruh tes
	$(PY) -m pytest -q

test-cepat:  ## Jalankan tes, berhenti pada kegagalan pertama
	$(PY) -m pytest -x -q

eval:  ## Ukur akurasi terhadap eval/golden/
	$(PY) -m eval.run_eval

buat-base:  ## Buat base NocoDB (relasi sebagai Link) untuk dol-schema terpasang [JUDUL=...]
	@$(LOAD_ENV) $(PY) scripts/nocodb_setup.py --create-base $(if $(JUDUL),"$(JUDUL)")

cek-nocodb:  ## Periksa URL, token, dan tableId NocoDB satu per satu
	@$(LOAD_ENV) $(PY) scripts/nocodb_setup.py --check

cek-skema:  ## Cocokkan tabel NocoDB dengan dol-schema, kolom per kolom
	@$(LOAD_ENV) $(PY) scripts/nocodb_setup.py --compare-schema

cek-ocr:  ## Periksa mesin OCR mana yang tersedia di mesin ini
	$(PY) scripts/cek_ocr.py

cek-rahasia:  ## Pindai kredensial sebelum push -- repo ini PUBLIK
	@echo "→ memindai working tree..."
	@! grep -rnE 'nc_pat_[A-Za-z0-9]{10,}|xc-token: *[A-Za-z0-9]{10,}' \
		--include='*.py' --include='*.md' --include='*.yml' --include='*.json' \
		--exclude-dir=.venv311 --exclude-dir=.git . \
		|| (echo "\033[31m✗ kredensial terdeteksi di atas — JANGAN push\033[0m"; exit 1)
	@echo "→ memindai setiap commit di cabang ini..."
	@# Memeriksa ISI POHON tiap commit, bukan `git log -S`: -S juga cocok pada commit
	@# yang MENGHAPUS token, sehingga perbaikan sendiri ikut dilaporkan sebagai temuan.
	@! git grep -I -E 'nc_pat_[A-Za-z0-9]{10,}' $$(git rev-list HEAD) -- \
		'*.py' '*.md' '*.yml' '*.json' 2>/dev/null \
		|| (echo "\033[31m✗ token ada di dalam riwayat cabang ini — jangan push, tulis ulang dulu\033[0m"; exit 1)
	@echo "→ memeriksa .env tidak ter-track..."
	@! git ls-files --error-unmatch .env >/dev/null 2>&1 \
		|| (echo "\033[31m✗ .env ter-track git\033[0m"; exit 1)
	@echo "→ memeriksa cabang lain..."
	@for b in $$(git for-each-ref --format='%(refname:short)' refs/heads/ | grep -v "^$$(git rev-parse --abbrev-ref HEAD)$$"); do \
		if git grep -I -q -E 'nc_pat_[A-Za-z0-9]{10,}' $$(git rev-list $$b -- 2>/dev/null) -- '*.py' 2>/dev/null; then \
			echo "\033[33m⚠ cabang '$$b' memuat token — jangan di-push, hapus setelah tidak diperlukan\033[0m"; \
		fi; \
	done
	@echo "\033[32m✓ cabang ini aman di-push\033[0m"

# --------------------------------------------------------------------------- menjalankan
serve:  ## Jalankan API di port 8000 (membaca .env)
	@$(LOAD_ENV) $(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000

uji-jobs:  ## Uji jalur n8n tanpa n8n: FILE=... ke /api/v1/jobs + push NocoDB, tunggu hasil
	@test -n "$(FILE)" || (echo "pakai: make uji-jobs FILE=path/SPH.pdf [DOC_TYPE=sph]"; exit 1)
	@$(LOAD_ENV) \
	job=$$(curl -sS --fail-with-body -H "X-API-Key: $$OPENADE_API_KEY" -F "file=@$(FILE)" \
		-F "doc_type=$(DOC_TYPE)" -F "push_to_nocodb=true" $(API)/api/v1/jobs) \
		|| { echo "$$job"; echo "✗ ditolak. API hidup (make serve)? NOCODB_PUSH_ENABLED=1 di .env?"; exit 1; }; \
	id=$$(echo "$$job" | $(PY) -c 'import json,sys; print(json.load(sys.stdin)["job_id"])'); \
	echo "→ $$id diantrikan; satu dokumen ±3-5 menit"; \
	while :; do \
		s=$$(curl -sS -H "X-API-Key: $$OPENADE_API_KEY" $(API)/api/v1/jobs/$$id); \
		st=$$(echo "$$s" | $(PY) -c 'import json,sys; print(json.load(sys.stdin)["status"])'); \
		case $$st in done|failed) echo "$$s" | $(PY) -m json.tool; break;; esac; \
		printf "."; sleep 15; \
	done

dok:  ## Buka dokumentasi API interaktif
	@echo "http://localhost:8000/docs"
	@open http://localhost:8000/docs 2>/dev/null || xdg-open http://localhost:8000/docs 2>/dev/null || true

bersih:  ## Hapus cache Python dan pytest
	find . -type d -name __pycache__ -not -path './.venv311/*' -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache
	@echo "cache dibersihkan"
