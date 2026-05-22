# JamSpell Russian spell-correction microservice.
#
# JamSpell is a C++ engine with a SWIG-built Python binding — `pip install
# jamspell` needs build tools, which we keep isolated to this image so the
# main backend doesn't drag swig + a C++ toolchain around.
#
# Methodology compliance: runs on team-controlled docker, no outbound calls.

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential swig wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        jamspell \
        fastapi \
        'uvicorn[standard]' \
        pydantic

# Fetch the Russian JamSpell model (bakwc/JamSpell-models — ru.tar.gz, the
# only RU model the project officially ships). server.py auto-discovers
# whatever .bin lands here, so this works whether the archive contains
# ru.bin or ru_small.bin.
RUN mkdir -p /opt/model && \
    wget -qO /tmp/ru.tar.gz \
        https://github.com/bakwc/JamSpell-models/raw/master/ru.tar.gz && \
    tar -xf /tmp/ru.tar.gz -C /opt/model && \
    rm /tmp/ru.tar.gz && \
    ls -lh /opt/model

WORKDIR /app
COPY server.py .

EXPOSE 8080

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
