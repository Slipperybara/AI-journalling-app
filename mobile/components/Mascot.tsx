import { Image, type ImageStyle, type StyleProp } from 'react-native';

// Static require map — Metro needs literal paths, so moods can't be built
// dynamically. Backgrounds were floodfilled to transparent (see assets/mascot/).
const SOURCES = {
  base: require('../assets/mascot/mascot-base.png'),
  happy: require('../assets/mascot/mascot-happy.png'),
  sad: require('../assets/mascot/mascot-sad.png'),
  thinkExcited: require('../assets/mascot/mascot-think-excited.png'),
  thinkSad: require('../assets/mascot/mascot-think-sad.png'),
  writing: require('../assets/mascot/mascot-writing.png'),
} as const;

export type MascotMood = keyof typeof SOURCES;

// Renders the JAI duck at a square box; `contain` keeps each pose's aspect ratio.
export function Mascot({
  mood = 'base',
  size = 120,
  style,
}: {
  mood?: MascotMood;
  size?: number;
  style?: StyleProp<ImageStyle>;
}) {
  return (
    <Image
      source={SOURCES[mood]}
      style={[{ width: size, height: size }, style]}
      resizeMode="contain"
    />
  );
}
