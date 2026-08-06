import React from "react";
import { SafeAreaView, StatusBar } from "react-native";
import DashboardScreen from "./src/screens/DashboardScreen";

export default function App() {
  return (
    <SafeAreaView style={{ flex: 1 }}>
      <StatusBar barStyle="dark-content" />
      <DashboardScreen />
    </SafeAreaView>
  );
}
