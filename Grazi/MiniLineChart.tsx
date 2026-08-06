import React from "react";
import { View } from "react-native";
import Svg, { Polyline } from "react-native-svg";

interface Props {
  dados: number[];
  largura?: number;
  altura?: number;
  cor?: string;
}

export default function MiniLineChart({
  dados,
  largura = 280,
  altura = 70,
  cor = "#1D9E75",
}: Props) {
  if (!dados || dados.length === 0) {
    return <View style={{ width: largura, height: altura }} />;
  }

  const max = Math.max(...dados);
  const min = Math.min(...dados);
  const range = max - min || 1;

  const pontos = dados
    .map((valor, i) => {
      const x = (i / (dados.length - 1)) * largura;
      const y = altura - ((valor - min) / range) * altura;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <Svg width={largura} height={altura}>
      <Polyline
        points={pontos}
        fill="none"
        stroke={cor}
        strokeWidth={2.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Svg>
  );
}
