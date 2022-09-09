FROM python:3.10

RUN python -m pip install --upgrade pip
RUN python -m pip install --upgrade wheel

RUN python -m pip install git+https://github.com/indralab/mira.git@add-bioregistry#egg=mira[metaregistry,dkg-client]
ENTRYPOINT python -m mira.dkg.metaregistry web --port 8772 --host "0.0.0.0" --with-gunicorn --workers 4
