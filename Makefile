setup:
	python -m pip install -r requirements.txt

seed:
	python -m app --seed

reseed:
	python -m app --seed --reset

run:
	python -m uvicorn app:app --reload --port 8000

test:
	python -m unittest discover -s tests -v
