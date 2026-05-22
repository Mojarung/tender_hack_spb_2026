# JamSpell Russian spell-correction microservice.
#
# JamSpell is a C++ engine with a SWIG-built Python binding. Modern Debian
# (bookworm) ships SWIG 4.x but JamSpell's setup.py asserts "SWIG Version 3"
# — we sed-patch that single check, because the SWIG-4 generated code
# actually compiles JamSpell fine.
#
# Methodology compliance: runs on team-controlled docker, no outbound calls.

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential swig wget ca-certificates locales \
    && sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
    && sed -i 's/# ru_RU.UTF-8 UTF-8/ru_RU.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

# JamSpell's jamspell/utils.cpp hardcodes locale "en_US.utf-8" (lowercase
# utf-8 with hyphen — non-standard). Without both env vars AND patched
# locale name the container crashes immediately with
# `locale::facet::_S_create_c_locale name not valid`.
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# Fetch JamSpell sdist, patch the SWIG version check + the missing
# <cstdint> includes (GCC 12 on bookworm is strict, JamSpell's old code
# uses uint32_t without including the header), build + install.
RUN pip install --no-cache-dir --upgrade pip && \
    mkdir -p /tmp/jam && \
    pip download --no-binary=:all: --no-deps jamspell -d /tmp/jam && \
    cd /tmp/jam && tar -xzf jamspell-*.tar.gz && rm jamspell-*.tar.gz && \
    cd jamspell-* && \
    sed -i "s|b'SWIG Version 3'|b'SWIG Version'|g" setup.py && \
    find . \( -name '*.hpp' -o -name '*.h' \) -exec \
        sed -i '1i #include <cstdint>' {} + && \
    find . -name '*.cpp' -exec \
        sed -i 's|"en_US.utf-8"|"en_US.UTF-8"|g' {} + && \
    pip install --no-cache-dir . && \
    cd / && rm -rf /tmp/jam

RUN pip install --no-cache-dir fastapi 'uvicorn[standard]' pydantic

# Russian JamSpell model from bakwc/JamSpell-models (the only RU model the
# project ships). server.py auto-discovers whatever *.bin lands here.
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
