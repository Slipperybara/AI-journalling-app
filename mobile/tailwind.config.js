/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './App.tsx',
    './app/**/*.{js,jsx,ts,tsx}',
    './components/**/*.{js,jsx,ts,tsx}',
    './screens/**/*.{js,jsx,ts,tsx}',
  ],
  presets: [require('nativewind/preset')],
  theme: {
    extend: {
      colors: {
        // Editorial palette mirrored from the web app (journal-frontend).
        paper: '#ECE9E4',
        ink: '#2A2825',
        'ink-soft': '#38342F',
        muted: '#8E8B84',
        'muted-soft': '#9A9790',
        faint: '#B7B4AD',
        track: '#ECEAE5',
        emotional: '#E0894F',
        physical: '#6E9B7A',
        focus: '#6E86C4',
        journaled: '#6E9B7A',
      },
    },
  },
  plugins: [],
};
