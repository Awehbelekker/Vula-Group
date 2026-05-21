import { useRouter } from "expo-router";
import { TouchableOpacity, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import HomeScreen from "../src/screens/HomeScreen";

export default function Index() {
  const router = useRouter();

  return (
    <View style={{ flex: 1 }}>
      <HomeScreen
        headerRight={
          <TouchableOpacity onPress={() => router.push("/settings")} style={{ marginRight: 16 }}>
            <Ionicons name="settings-outline" size={22} color="#2C5545" />
          </TouchableOpacity>
        }
      />
    </View>
  );
}
