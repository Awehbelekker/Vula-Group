import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";

export default function RootLayout() {
  return (
    <>
      <StatusBar style="dark" />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: "#FFFFFF" },
          headerTintColor: "#2C5545",
          headerTitleStyle: { fontWeight: "700" },
          contentStyle: { backgroundColor: "#F7F4EE" },
        }}
      >
        <Stack.Screen name="index" options={{ title: "Vula" }} />
        <Stack.Screen name="documents" options={{ title: "My Documents" }} />
        <Stack.Screen name="settings" options={{ title: "Settings" }} />
      </Stack>
    </>
  );
}
