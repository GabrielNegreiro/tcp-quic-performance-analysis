# Certificados locais

Ao iniciar `server-http3` pela primeira vez, o contêiner gera neste diretório:

- `server-http3.crt`: certificado autoassinado do laboratório;
- `server-http3.key`: chave privada correspondente.

O certificado inclui `server-http3`, `localhost`, `172.20.0.30` e `127.0.0.1` no SAN. Ele é válido por 365 dias e o Caddyfile restringe o servidor a TLS 1.3.

Os arquivos gerados não são versionados. Em especial, a chave privada nunca deve ser adicionada ao Git. Apagar ambos os arquivos com os serviços encerrados faz com que um novo par seja criado no próximo `docker compose up`.

O uso de certificado autoassinado e `--insecure` é aceitável apenas para diagnóstico neste laboratório. Os scripts de validação usam o certificado explicitamente com `--cacert` sempre que possível.
