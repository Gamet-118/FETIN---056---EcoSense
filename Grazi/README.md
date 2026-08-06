# EcoSense — app mobile (capa / front-end)

Esqueleto do dashboard mobile do EcoSense, feito em **React Native + Expo**.
Hoje todos os dados são **mockados** (gerados aleatoriamente), mas a estrutura
já está pronta para plugar os dados reais do sensor/backend sem mexer nas telas.

## Como rodar (VSCode)

1. Instale o Node.js (LTS) e o Expo CLI, se ainda não tiver:
   ```
   npm install -g expo-cli
   ```
2. Dentro da pasta do projeto:
   ```
   npm install
   npm start
   ```
3. Escaneie o QR code com o app **Expo Go** (Android/iOS) para ver rodando no celular,
   ou aperte `w` no terminal para abrir no navegador.

## Estrutura

```
ecosense-app/
├── App.tsx                        # ponto de entrada
├── src/
│   ├── types/
│   │   └── EnergyReading.ts       # contrato de dados (o que o backend deve enviar)
│   ├── services/
│   │   └── energyService.ts       # ⚠️ ÚNICO arquivo a trocar quando o back estiver pronto
│   ├── components/
│   │   └── MiniLineChart.tsx      # gráfico de linha simples em SVG
│   └── screens/
│       └── DashboardScreen.tsx    # tela principal do dashboard
```

## Como plugar o dado real depois

Tudo se resume a editar `src/services/energyService.ts`. A função
`fetchEnergyReading` precisa continuar retornando um objeto no formato
`EnergyReading` (veja `src/types/EnergyReading.ts`) — o resto do app não
precisa de nenhuma alteração.

Alguns caminhos possíveis, dependendo de como o back/firmware vai expor o dado:

- **API REST** (ESP32 manda pra um servidor, app consulta por HTTP):
  ```ts
  export async function fetchEnergyReading(deviceId: string) {
    const res = await fetch(`https://SEU_BACKEND/api/devices/${deviceId}`);
    return (await res.json()) as EnergyReading;
  }
  ```
- **WebSocket / MQTT** (dado chega em tempo real, sem precisar dar polling)
- **Bluetooth (BLE)** direto do ESP32 pro celular (biblioteca `react-native-ble-plx`)

## Próximos passos sugeridos

- Tela de histórico/gráfico detalhado (consumo por dia/semana)
- Tela de configuração do dispositivo (nome, local, limites de alerta)
- Notificação push quando o consumo passar de um limite
- Suporte a múltiplos dispositivos (várias "folhas" na casa)
