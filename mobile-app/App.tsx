import React, { useEffect, useState } from "react";
import { View, ActivityIndicator } from "react-native";
import { StatusBar } from "expo-status-bar";
import { NavigationContainer, DarkTheme, Theme } from "@react-navigation/native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { C } from "./src/theme";
import { loadConnection } from "./src/storage";
import { configureApi } from "./src/api";
import PairingScreen from "./src/screens/PairingScreen";
import MainTabs from "./src/MainTabs";
import MediaControlBridge from "./src/MediaControlBridge";
import GamepadOverlay from "./src/components/GamepadOverlay";

// React Navigation defaults to a WHITE screen background — invisible most of
// the time since our own screens paint over it, but it flashes through
// during transitions/animations (e.g. the keyboard resize on Android) where
// the navigator's own container is momentarily visible at the edges.
const navTheme: Theme = {
  ...DarkTheme,
  colors: {
    ...DarkTheme.colors,
    background: C.bg,
    card: C.panel,
    text: C.text,
    border: C.borderA,
    primary: C.pri,
  },
};

export default function App() {
  const [checking, setChecking] = useState(true);
  const [paired, setPaired] = useState(false);

  useEffect(() => {
    (async () => {
      const conn = await loadConnection();
      if (conn) {
        configureApi(conn);
        setPaired(true);
      }
      setChecking(false);
    })();
  }, []);

  if (checking) {
    return (
      <View style={{ flex: 1, backgroundColor: C.bg, alignItems: "center", justifyContent: "center" }}>
        <ActivityIndicator color={C.pri} />
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <NavigationContainer theme={navTheme}>
        <StatusBar style="light" />
        {paired ? (
          // flex:1 wrapper so the gamepad banner, which is absolutely
          // positioned, anchors to the full screen rather than collapsing.
          <View style={{ flex: 1 }}>
            {/* Renders nothing; mirrors playback into the system media
                controls and routes their buttons back to the desktop. */}
            <MediaControlBridge />
            <MainTabs onForget={() => setPaired(false)} />
            {/* Offers itself only while a game is actually running on the PC. */}
            <GamepadOverlay />
          </View>
        ) : (
          <PairingScreen onPaired={() => setPaired(true)} />
        )}
      </NavigationContainer>
    </SafeAreaProvider>
  );
}
