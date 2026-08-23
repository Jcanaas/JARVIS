import React from "react";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { C } from "./theme";
import ChatScreen from "./screens/ChatScreen";
import MusicScreen from "./screens/MusicScreen";
import WhatsAppScreen from "./screens/WhatsAppScreen";
import CalendarScreen from "./screens/CalendarScreen";
import RemoteScreen from "./screens/RemoteScreen";
import SettingsScreen from "./screens/SettingsScreen";

const Tab = createBottomTabNavigator();

export default function MainTabs({ onForget }: { onForget: () => void }) {
  const insets = useSafeAreaInsets();

  return (
    <Tab.Navigator
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: C.pri,
        tabBarInactiveTintColor: C.textMed,
        tabBarStyle: {
          backgroundColor: "rgba(13,16,32,0.98)",
          borderTopColor: C.borderA,
          height: 54 + insets.bottom,
          paddingBottom: Math.max(6, insets.bottom),
          paddingTop: 6,
        },
        tabBarLabelStyle: { fontSize: 10, fontWeight: "700" },
      }}
    >
      <Tab.Screen
        name="Chat"
        options={{ tabBarIcon: ({ color, size }) => <Ionicons name="chatbubble-ellipses" color={color} size={size} /> }}
      >
        {({ navigation }) => <ChatScreen onOpenMusic={() => navigation.navigate("Música")} />}
      </Tab.Screen>
      <Tab.Screen
        name="Música"
        component={MusicScreen}
        options={{ tabBarIcon: ({ color, size }) => <Ionicons name="musical-notes" color={color} size={size} /> }}
      />
      <Tab.Screen
        name="WhatsApp"
        component={WhatsAppScreen}
        options={{ tabBarIcon: ({ color, size }) => <Ionicons name="logo-whatsapp" color={color} size={size} /> }}
      />
      <Tab.Screen
        name="Agenda"
        component={CalendarScreen}
        options={{ tabBarIcon: ({ color, size }) => <Ionicons name="calendar" color={color} size={size} /> }}
      />
      <Tab.Screen
        name="PC"
        component={RemoteScreen}
        options={{ tabBarIcon: ({ color, size }) => <Ionicons name="desktop" color={color} size={size} /> }}
      />
      <Tab.Screen name="Ajustes" options={{ tabBarIcon: ({ color, size }) => <Ionicons name="settings-sharp" color={color} size={size} /> }}>
        {() => <SettingsScreen onForget={onForget} />}
      </Tab.Screen>
    </Tab.Navigator>
  );
}
