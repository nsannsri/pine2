const CDP = require('chrome-remote-interface');

async function main() {
    const targets = await CDP.List({port: 9222});
    const tvTarget = targets.find(t => t.url.includes('tradingview.com/chart'));
    if (!tvTarget) { console.error('No TradingView chart tab found!'); process.exit(1); }

    console.log('Connected to:', tvTarget.url);
    const client = await CDP({target: tvTarget.webSocketDebuggerUrl, port: 9222});
    const {Network} = client;

    await Network.enable();

    Network.requestWillBeSent(({request}) => {
        const url = request.url;
        const isPost = request.method === 'POST' || request.method === 'PUT' || request.method === 'PATCH';
        const isRelevant = url.includes('pine') || url.includes('publish') ||
                           url.includes('script') || url.includes('algoservice') ||
                           url.includes('pubscript');

        if (isPost || isRelevant) {
            console.log(`\n>>> ${request.method} ${url}`);
            if (request.postData) {
                console.log('    BODY:', request.postData.substring(0, 1000));
            }
        }
    });

    console.log('\n' + '='.repeat(60));
    console.log('NOW: In TradingView Pine Editor, click:');
    console.log('  "Publish script" → "Update existing publication"');
    console.log('Waiting 90 seconds for you to click...');
    console.log('='.repeat(60) + '\n');

    await new Promise(resolve => setTimeout(resolve, 90000));
    client.close();
    process.exit(0);
}

main().catch(e => { console.error(e.message); process.exit(1); });
