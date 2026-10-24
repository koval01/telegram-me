# Capa de pares

Con varios servidores (o workers) en una misma red privada, los nodos de
TelegramMe pueden compartir su trabajo directamente, sin Redis o junto a él.
Cada clave de caché tiene un propietario entre los nodos vivos. Solo el
propietario descarga la clave de t.me y la renderiza; los demás nodos se la
piden y guardan su respuesta. La capa está desactivada por defecto
([`ENABLE_PEERS`](../configuration.md#enable_peers)), y sin ella todo funciona
como antes.

## Cómo funciona { #how-it-works }

- **Nodos.** Cada proceso worker es un nodo. Los nodos se encuentran a través
  de [`PEERS`](../configuration.md#peers) (direcciones o nombres DNS) y, con
  Redis, a través de los marcadores de latido de los demás. Siguen quién está
  vivo con gossip SWIM sobre UDP.
- **Propietarios.** El propietario de una clave es el nodo vivo que gana un
  hash de rendezvous del canal de la clave, de modo que todas las claves de un
  canal (páginas, cuerpos y publicaciones renderizados, fragmentos "more", la
  vista previa) tienen el mismo propietario. Cada nodo calcula el mismo
  propietario por su cuenta; un nodo que se va o llega solo mueve los canales
  que posee.
- **Un fallo de caché.** Ante un fallo de la caché dentro del proceso, un nodo
  pregunta al propietario por TCP. El propietario responde desde su caché
  dentro del proceso directamente en el código nativo, sin Python. Quien
  pregunta guarda la copia durante el TTL que le queda a la entrada.
- **Una carga.** Si el propietario tampoco tiene la clave, quien pregunta le
  pide que la cargue. El propietario se une a cualquier trabajo idéntico en
  curso, descarga una sola vez y envía el valor (una página viaja con su cuerpo
  renderizado) o el error. Con los pares activos, esto sustituye al lease de
  Redis.
- **Coordinación.** El actualizador del feed se ejecuta en el nodo vivo con la
  dirección más baja que puede ejecutarlo. Sin Redis, los nodos también se
  envían una vez por segundo sus recuentos del límite de peticiones y los
  impactos de popularidad del feed.

## Activarla { #turning-it-on }

En cada nodo:

```bash
ENABLE_PEERS=true
PEER_SECRET=<the same 64 hex characters everywhere>   # openssl rand -hex 32
PEERS=10.0.0.108,10.0.0.165
# In a container, the host's private address (the production deploy sets it):
PEER_ADVERTISE_ADDR=10.0.0.108
```

Cada worker escucha en el primer puerto libre a partir de
[`PEER_PORT`](../configuration.md#peer_port) (7946), para UDP y TCP, así que
abre de `PEER_PORT` a `PEER_PORT + WEB_CONCURRENCY` entre los nodos. El log
muestra `Peer layer on: this node is 10.0.0.108:7946` y luego `Peers: …` cada
vez que cambian los miembros vivos.

## Descubrimiento { #discovery }

La capa busca otros nodos en este orden:

1. **`PEERS`**: direcciones, pares `host:port` o nombres DNS. Los nombres se
   resuelven de nuevo cada 30 segundos (cada 2 mientras no se conoce otro
   nodo), así que un nombre cuyos registros enumeran los servidores sigue
   funcionando a medida que los servidores van y vienen.
2. **Redis**: con [`REDIS_URL`](../configuration.md#redis_url), el marcador de
   latido de cada worker `tme:v2:node:<host>:<pid>` lleva su dirección de par
   (`"peer": "10.0.0.108:7946"`), y cada nodo lee los marcadores cada
   10 segundos.

No hay multicast: redes en la nube como las VCN de Oracle Cloud no lo
transportan. Basta con un nodo alcanzable para unirse; el resto es gossip.

## Seguridad { #security }

- **Autenticación.** Cada trama (datagrama UDP o mensaje TCP) lleva la hora
  del emisor, un nonce único y una etiqueta HMAC-SHA256 con una clave derivada
  de [`PEER_SECRET`](../configuration.md#peer_secret). Una trama cuya etiqueta
  no se verifica, cuya hora se desvía más de 30 segundos o cuyo nonce ya se vio
  se descarta, sin analizar antes nada de ella.
- **Cifrado.** Con [`PEER_ENCRYPTION`](../configuration.md#peer_encryption)
  las tramas se cifran en su lugar con XChaCha20-Poly1305. Sin él, los datos
  públicos de canales en caché cruzan la red en claro, autenticados.
- **Redes.** Los pares fuera de
  [`PEER_ALLOW_CIDR`](../configuration.md#peer_allow_cidr) se rechazan antes de
  leer un solo byte.
- **Límites.** Las tramas tienen como máximo 8 MiB, las claves 1 KiB, y el
  número de conexiones entrantes y de cargas en espera está acotado.

Todo el clúster comparte el secreto: rótalo en todos los nodos a la vez. El
cifrado usa una clave estática derivada de él, así que no ofrece secreto hacia
adelante; TLS mutuo con certificados por nodo no está implementado.

## Cortafuegos y Docker { #firewall-and-docker }

`deploy/bootstrap-server.sh` abre de `PEER_PORT` a `PEER_PORT + 15`, UDP y
TCP, en el cortafuegos del host solo para la red privada (`PEER_CIDR`, por
defecto `10.0.0.0/16`). Las imágenes de Oracle Cloud rechazan cualquier otro
puerto con "No route to host"; la lista de seguridad de la nube también debe
permitir los puertos entre los servidores.

`deploy/compose.prod.yaml` publica los puertos de pares del contenedor de la
API en `PEER_BIND`. Con `ENABLE_PEERS=true`, `deploy/remote-deploy.sh` lo
configura, junto con `PEER_ADVERTISE_ADDR`, con la dirección privada del
servidor (la dirección de origen de su ruta por defecto), y publica un puerto
por worker más uno.

El workflow de despliegue activa la capa con la variable `ENABLE_PEERS` y el
secreto `PEER_SECRET`; `PEERS`, `PEER_PORT`, `PEER_ALLOW_CIDR` y
`PEER_ENCRYPTION` son variables opcionales.

## Cuando algo falla { #when-something-fails }

Ningún fallo de un par hace fallar una solicitud; quien pregunta hace el
trabajo por su cuenta:

| Situación | Qué ocurre |
|---|---|
| El propietario no se conecta ni responde a una lectura en [`PEER_TIMEOUT`](../configuration.md#peer_timeout) | Se lee Redis, o la clave se carga localmente; se evita al propietario durante 3 s |
| El propietario no acepta una carga en `PEER_TIMEOUT` | Quien pregunta carga la clave, con el lease de Redis si hay Redis |
| El propietario aceptó una carga y no responde | Quien pregunta espera lo mismo que a un titular del lease de Redis y luego la carga |
| El propietario se cayó | Su puerto rechaza al instante; el gossip lo declara caído en unos segundos |
| Un nodo con otro secreto, o fuera de `PEER_ALLOW_CIDR` | Sus tramas se descartan y se registran; nunca se une |
| Dos nodos discrepan un momento sobre los miembros | Una clave puede descargarse dos veces, o dos nodos actualizan el feed; nada más |

## Mediciones { #measurements }

En una sola máquina (Apple serie M, todo en localhost):

- **Un fallo respondido por el propietario** (una página en caché de 56 KB):
  0,18 ms de mediana, 0,35 ms en p99, con o sin cifrado. A través de un Redis
  a 90 ms tarda 95 ms; con un Redis local, 0,6 ms.
- **Carga con muchos duplicados** (`scripts/coalescing_load.py`, todos los
  endpoints de todos los canales grabados, t.me respondiendo en 150 ms, 20 s):

| Nodos | Coordinación | Descargas de t.me | Latencia p99 |
|---|---|---|---|
| 2 | ninguna | 138 | 147 ms |
| 2 | lease de Redis, Redis a 90 ms | 70 | 188 ms |
| 2 | pares, sin Redis | 69 | 163 ms |
| 6 | ninguna | 414 | 156 ms |
| 6 | lease de Redis, Redis a 90 ms | 75 | 164 ms |
| 6 | pares, sin Redis | 79 | 118 ms |

Las 27 solicitudes distintas al origen caducan más o menos cada 10 s, así que
unas 2,5 descargas por solicitud equivalen a una descarga por clave y TTL para
todo el clúster.
