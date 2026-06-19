import { useEffect, useRef } from 'react';
import { Animated, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

import { gradients } from '../lib/theme';

// Mirrors the web app's ambient tint: warm while conversing, cool while the bot
// searches the knowledge graph (the analytical / retrieval path). The warm layer
// is always painted; the cool layer crossfades over it on a 1.1s ease, matching
// journal-frontend's BG_WARM / BG_COOL.
const WARM = gradients.warm as [string, string, ...string[]];
const COOL = gradients.cool as [string, string, ...string[]];

export function AmbientBackground({ mode }: { mode: 'warm' | 'cool' }) {
  const coolOpacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.timing(coolOpacity, {
      toValue: mode === 'cool' ? 1 : 0,
      duration: 1100,
      useNativeDriver: true,
    }).start();
  }, [mode, coolOpacity]);

  return (
    <>
      <LinearGradient
        colors={WARM}
        start={{ x: 0.05, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={StyleSheet.absoluteFill}
      />
      <Animated.View style={[StyleSheet.absoluteFill, { opacity: coolOpacity }]} pointerEvents="none">
        <LinearGradient
          colors={COOL}
          start={{ x: 0.05, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={StyleSheet.absoluteFill}
        />
      </Animated.View>
    </>
  );
}
