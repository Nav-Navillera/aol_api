// Copyright (c) 2025, Navi and contributors
// For license information, please see license.txt

frappe.ui.form.on('AOL API Settings', {
    refresh: function(frm) {
        frm.add_custom_button(__('Request Access Token'), function() {
            const client_id = frm.doc.client_id;
            const redirect_url = frm.doc.oauth_callback_url;
            const scope = frm.doc.scope;

            const url = `https://account.accurate.id/oauth/authorize?client_id=${client_id}&response_type=code&redirect_uri=${redirect_url}&scope=${scope}`;

            // Redirect to Accurate's authorization URL
            window.open(url, '_blank');
        });
    }
});
