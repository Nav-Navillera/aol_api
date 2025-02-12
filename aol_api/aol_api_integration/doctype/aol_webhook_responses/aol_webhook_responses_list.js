// Copyright (c) 2025, Navi and contributors
// For license information, please see license.txt

frappe.listview_settings['AOL Webhook Responses'] = {
    onload: function(listview) {
        listview.page.add_inner_button('Sync Webhook', function() {
            frappe.call({
                method: 'aol_api.webhook.sync_webhook',
                args: {},
                callback: function(response) {
                    if (response.message === "success") {
                        frappe.msgprint(__('Webhook sync completed successfully.'));
                        listview.refresh();  // Refresh list setelah sync
                    } else {
                        frappe.msgprint(__('Error: ' + response.message));
                    }
                }
            });
        });
    }
};
