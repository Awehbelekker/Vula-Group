import { useEffect, useState } from "react";
import { Stack, useRouter, useSegments } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { getSession } from "../src/api/vula";

function AuthGuard({ children }) {
  const router = useRouter();
  const segments = useSegments();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    getSession().then((session) => {
      const inAuth = segments[0] === "login";
      if (!session && !inAuth) {
        router.replace("/login");
      } else if (session && inAuth) {
        router.replace("/");
      }
      setChecked(true);
    });
  }, [segments]);

  if (!checked) return null;
  return children;
}

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
        <Stack.Screen name="login" options={{ headerShown: false }} />
        <Stack.Screen name="index" options={{ title: "Vula" }} />
        <Stack.Screen name="documents" options={{ title: "My Documents" }} />
        <Stack.Screen name="settings" options={{ title: "Settings" }} />
      </Stack>
    </>
  );
}
