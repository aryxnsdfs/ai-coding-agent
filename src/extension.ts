// The module 'vscode' contains the VS Code extensibility API
// Import the module and reference it with the alias vscode in your code below
import * as vscode from 'vscode';
import Anthropic from '@anthropic-ai/sdk';
import { GoogleGenAI } from '@google/genai';
// This method is called when your extension is activated
// Your extension is activated the very first time the command is executed
export function activate(context: vscode.ExtensionContext) {

	// Use the console to output diagnostic information (console.log) and errors (console.error)
	// This line of code will only be executed once when your extension is activated
	console.log('Congratulations, your extension "tt" is now active!');

	// The command has been defined in the package.json file
	// Now provide the implementation of the command with registerCommand
	// The commandId parameter must match the command field in package.json
	const disposable = vscode.commands.registerCommand('tt.askAI', async () => {
		const editor = vscode.window.activeTextEditor;
		if (!editor) {
			vscode.window.showErrorMessage('No active file open to read.');
			return;
		}

		// Grab the user's highlighted code, or the whole file if nothing is highlighted
		const textToAnalyze = editor.document.getText(editor.selection) || editor.document.getText();
		
		// Prompt for keys (Next step: move this to SecretStorage)
		const modelChoice = await vscode.window.showQuickPick(['Claude', 'Gemini'], {
            placeHolder: 'Which model do you want to use?'
        });
        
        if (!modelChoice) { return; }

        const apiKey = await vscode.window.showInputBox({ 
            prompt: `Enter ${modelChoice} API Key (Temporary)`, 
            password: true 
        });
        if (!apiKey) { return; }

        vscode.window.showInformationMessage(`Analyzing code with ${modelChoice}...`);

        try {
            let aiResponse = '';

            if (modelChoice === 'Claude') {
                const anthropic = new Anthropic({ apiKey });
                const response = await anthropic.messages.create({
                    model: 'claude-3-5-sonnet-latest',
                    max_tokens: 1024,
                    messages: [{ role: 'user', content: `Explain this code briefly:\n\n${textToAnalyze}` }]
                });
                aiResponse = response.content[0].type === 'text' ? response.content[0].text : 'No text returned.';
            } else {
                const ai = new GoogleGenAI({ apiKey });
                const response = await ai.models.generateContent({
                    model: 'gemini-3-pro',
                    contents: `Explain this code briefly:\n\n${textToAnalyze}`,
                });
                aiResponse = response.text || 'No text returned.';
            }

            vscode.window.showInformationMessage(`AI: ${aiResponse}`);
        } catch (error) {
            vscode.window.showErrorMessage(`AI Error: ${error}`);
        }
	});

	context.subscriptions.push(disposable);
}

// This method is called when your extension is deactivated
export function deactivate() {}
