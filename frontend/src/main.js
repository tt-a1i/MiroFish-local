import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { loadGraphTypeTranslations } from './store/graphTypeTranslations'

loadGraphTypeTranslations().catch(() => {})

const app = createApp(App)

app.use(router)

app.mount('#app')
