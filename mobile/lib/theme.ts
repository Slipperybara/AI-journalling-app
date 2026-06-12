// Editorial design tokens, mirrored from journal-frontend. Colors are also
// declared in tailwind.config.js for NativeWind className usage; this module
// is for the cases that need raw values (SVG fills, gradients, chart bars).

export const colors = {
  paper: '#ECE9E4',
  ink: '#2A2825',
  inkSoft: '#38342F',
  muted: '#8E8B84',
  mutedSoft: '#9A9790',
  faint: '#B7B4AD',
  line: 'rgba(0,0,0,0.06)',
  track: '#ECEAE5',
  emotional: '#E0894F',
  physical: '#6E9B7A',
  focus: '#6E86C4',
  journaled: '#6E9B7A',
};

// Warm (conversing) and cool (retrieval) ambient gradient stops — used later
// with expo-linear-gradient, matching the web app's BG_WARM / BG_COOL.
export const gradients = {
  warm: ['#ECE9E4', '#E5DED3', '#DFD6C7', '#DAD1C0'],
  cool: ['#E6E7E4', '#DBDFE2', '#CFD6DC', '#C7D0D9'],
};

// Font family names — loaded via expo-font in a later step (Lora + DM Sans).
// Until then, omit fontFamily to fall back to the system font.
export const fonts = { serif: 'Lora', sans: 'DMSans' };
